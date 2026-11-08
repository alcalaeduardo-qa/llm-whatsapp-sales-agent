import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app import (
    calendario,
    comandos,
    config,
    guardrails,
    limites,
    llm,
    metricas,
    recordatorios,
    store,
    telegram,
    transcripcion,
    whatsapp,
)
from app.prompt import huella as huella_del_prompt
from app.prompt import texto_del_prompt
from app.scheduling import dia_y_fecha
from app.text import describir, normalizar_numero, referencia

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# Las conexiones HTTP/3 del cliente de CalDAV escriben un aviso por minuto al cerrarse por inactividad.
logging.getLogger("quic").setLevel(logging.ERROR)
logger = logging.getLogger("sofia")


@asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    """Con el calendario configurado, revisa cada minuto si toca mandar recordatorios."""
    try:
        logger.info("Prompt cargado, huella %s", huella_del_prompt())
    except OSError:
        logger.warning("No hay prompt para calcular su huella")
    await telegram.preparar_bot()
    tarea = asyncio.create_task(recordatorios.bucle()) if calendario.configurado() else None
    if tarea is None:
        logger.warning("iCloud sin configurar: no se enviarán recordatorios de sesión")
    yield
    if tarea:
        tarea.cancel()


app = FastAPI(title="Sofía · WhatsApp sales agent", lifespan=ciclo_de_vida)


async def generar_validada(historial: list[dict], remitente: str, contexto: str | None = None) -> llm.Respuesta:
    """Respuesta de Claude que pasa el validador; un reintento con la corrección y luego respaldo fijo.

    Si Claude deriva a Eduardo no se reintenta: basta con que el texto sea válido o se usa uno fijo.
    """
    correccion = None
    for intento in (1, 2):
        respuesta = await llm.generar_respuesta(historial, correccion, contexto)
        problemas = guardrails.validar_salida(respuesta.texto, texto_del_prompt())
        if not respuesta.derivacion and not problemas and guardrails.promete_derivar(respuesta.texto):
            # Le dijo que Eduardo le escribirá pero no usó la herramienta: sin esto la alerta nunca llega
            respuesta.derivacion = {"motivo": "otro", "resumen": "Sofía le dijo que Eduardo le escribiría."}
        if respuesta.derivacion:
            if problemas:
                respuesta.texto = guardrails.RESPUESTA_DERIVACION
            return respuesta
        if not problemas:
            return respuesta
        if intento == 2 and all(guardrails.es_problema_de_largo(p) for p in problemas):
            # El largo es una regla de estilo: los evals mostraron respuestas de propuesta un poco largas que
            # terminaban en el respaldo y derivaban sin necesidad. Mejor un mensaje largo que dejar esperando.
            logger.warning("Se envía pese a %s (solo largo)", problemas)
            return respuesta
        correccion = ", ".join(problemas)
        logger.warning("Intento %s descartado por %s", intento, problemas)

    # El respaldo promete que Eduardo escribirá, así que la conversación pasa a él.
    return llm.Respuesta(
        guardrails.RESPUESTA_RESPALDO,
        {"motivo": "respaldo", "resumen": f"Sofía no logró una respuesta válida ({correccion})."},
    )


async def enviar(remitente: str, texto: str, pie: str = "") -> None:
    """Manda el mensaje y lo guarda en el historial. El pie llega a la persona pero no se guarda."""
    await store.guardar_respuesta(remitente, texto)
    await whatsapp.enviar_texto(normalizar_numero(remitente), f"{texto}\n\n{pie}" if pie else texto)


def _pie(remitente: str) -> str:
    return f"Contesta a este mensaje para responderle · /reanudar {remitente} para que Sofía siga"


async def derivar(remitente: str, motivo: str, resumen: str, ultimo_texto: str) -> None:
    """Eduardo toma la conversación: Sofía se calla y le reenvía lo que llegue."""
    await store.activar_humano(remitente, motivo)
    await telegram.alertar(
        f"🙋 Derivación de +{remitente} ({motivo})\n{resumen}\n\nÚltimo mensaje\n{ultimo_texto}\n\n{_pie(remitente)}"
    )


async def reenviar_a_eduardo(remitente: str, texto: str) -> None:
    """Modo humano: no responde el modelo. Una crisis explícita sí recibe la línea de ayuda; una encubierta
    (la que detecta el clasificador) solo se marca en la alerta, porque Eduardo está atendiendo."""
    prefijo = ""
    if guardrails.crisis_explicita(texto):
        prefijo = "🚨 Posible crisis, Sofía le compartió la Línea de la Vida.\n"
        try:
            await enviar(remitente, guardrails.respuesta_crisis(remitente))
        except Exception:
            logger.exception("No se pudo enviar la respuesta de crisis en modo humano")
    elif await guardrails.detectar_crisis([texto]):
        prefijo = (
            "🚨 Posible crisis encubierta (la detectó el clasificador). Sofía no le escribió: revisa y escríbele tú.\n"
        )
    await telegram.alertar(f"{prefijo}💬 +{remitente}\n{texto}\n\n{_pie(remitente)}")


async def atender_crisis(remitente: str, nueva: bool, texto: str = "") -> None:
    """Respuesta fija (no pasa por el modelo) y pausa de venta. A Eduardo solo se le confirma que el mensaje salió:
    la persona no debe esperar que alguien la contacte."""
    alerta = None
    if nueva:
        pausada = await store.pausar_venta(remitente, "crisis")
        respuesta = guardrails.respuesta_crisis(remitente)
        alerta = f"ℹ️ Sofía envió el mensaje de crisis a +{remitente}."
        if not pausada:
            alerta += f" NO pudo pausar la venta (falló la base de datos): usa /pausar {remitente}."
    else:
        respuesta = guardrails.respuesta_pausa(remitente, texto)

    try:
        try:
            await enviar(remitente, respuesta)
        except Exception:
            # Si falló el guardado en la base, la persona no se queda sin la línea de ayuda: se manda directo
            logger.exception("Falló el envío o el guardado de la respuesta de crisis; se reintenta directo")
            await whatsapp.enviar_texto(normalizar_numero(remitente), respuesta)
    finally:
        if alerta:
            await telegram.alertar(alerta)


async def aceptar_aviso(remitente: str, texto: str, wa_message_id: str) -> None:
    """Solo la persona quita el bloqueo, y queda el respaldo. Si no se pudo guardar, el bloqueo sigue."""
    registrada = await store.registrar_aceptacion(remitente, texto, wa_message_id, guardrails.VERSION_AVISO)
    if not registrada or not await store.reanudar(remitente):
        await atender_crisis(remitente, nueva=False)
        return
    await enviar(remitente, guardrails.RESPUESTA_ACEPTADO)
    await telegram.alertar(f"ℹ️ +{remitente} aceptó el aviso de coaching; quedó registrado.")


async def estado_desconocido(remitente: str, texto: str) -> None:
    """No se pudo leer si la conversación está en pausa por crisis o con Eduardo. El modelo no responde, porque
    podría romper una pausa; una crisis sí se atiende y, si no la hay, Eduardo se entera."""
    if await guardrails.detectar_crisis([texto]):
        await atender_crisis(remitente, nueva=True)
        return
    await telegram.alertar(
        f"⚠️ No pude leer el estado de +{remitente} (falló la base de datos) y no le respondí, para no romper una "
        f"pausa o un modo humano. Escríbele tú.\n\n{_pie(remitente)}"
    )


async def sin_guardado(remitente: str, texto: str) -> None:
    """No se pudo guardar el mensaje entrante. Sin historial ni estado Sofía no responde, pero una crisis no
    espera a la base de datos."""
    if await guardrails.detectar_crisis([texto]):
        try:
            await whatsapp.enviar_texto(normalizar_numero(remitente), guardrails.respuesta_crisis(remitente))
        except Exception:
            logger.exception("No se pudo enviar la respuesta de crisis directa")
        await telegram.alertar(
            f"🚨 Posible crisis de +{remitente}. Falló la base de datos: Sofía solo pudo mandar la Línea de la Vida. "
            f"Escríbele tú.\n\n{_pie(remitente)}"
        )
        return
    await telegram.alertar(
        f"⚠️ No pude guardar el mensaje de +{remitente} (falló la base de datos) y no le respondí. "
        f"Escríbele tú.\n\n{_pie(remitente)}"
    )


async def conversacion_cambio(remitente: str) -> dict | None:
    """Reconsulta el estado justo antes de enviar. Mientras el modelo piensa, otra tarea puede haber atendido una
    crisis o Eduardo puede haber tomado la conversación. Devuelve el estado si ya no se debe responder."""
    estado = await store.obtener_estado(remitente)
    if estado.get("desconocido"):
        return None  # ya se leyó bien hace unos segundos; un fallo ahora no justifica perder la respuesta
    return estado if estado["humano_activo"] or estado["venta_pausada"] else None


async def contexto_de(remitente: str, estado: dict, historial: list[dict] = ()) -> str | None:
    """Hechos que el historial no muestra: que Eduardo ya la atendió, sus sesiones ya reservadas
    y que hay notas de voz transcritas."""
    notas = []
    if estado["atendida_por_eduardo"]:
        notas.append(llm.NOTA_DEVUELTA)
    if guardrails.hubo_crisis(historial):
        notas.append(llm.NOTA_TRAS_CRISIS)
    if any(m["role"] == "user" and m["content"].startswith(guardrails.MARCA_AUDIO) for m in historial):
        notas.append(llm.NOTA_AUDIO)
    sesiones = await calendario.sesiones_de(remitente, datetime.now(config.ZONA_MX))
    if sesiones:
        cuando = " y el ".join(f"{dia_y_fecha(s.date())} a las {s.strftime('%I').lstrip('0')} PM" for s in sesiones)
        notas.append(llm.NOTA_SESION_RESERVADA.format(cuando=cuando))
    else:
        notas.append(llm.NOTA_SIN_RESERVA)
    return "\n\n".join(notas) or None


async def limite_excedido(remitente: str, texto: str, motivo: str) -> None:
    """Pasó un tope: no se llama a Claude ni se reenvía a Telegram. Una crisis explícita sí recibe la línea de
    ayuda (la detecta una lista de palabras, sin costo), y cada aviso sale una sola vez por ventana."""
    try:
        destino = normalizar_numero(remitente)
        if texto and guardrails.crisis_explicita(texto):
            await whatsapp.enviar_texto(destino, guardrails.respuesta_crisis(remitente))
        if motivo == "persona" and limites.primer_aviso(remitente, limites.HORA):
            await whatsapp.enviar_texto(destino, guardrails.RESPUESTA_LIMITE)
            await telegram.alertar(
                f"⚠️ +{remitente} mandó más de {config.LIMITE_MENSAJES_HORA} mensajes en una hora. Sofía deja de "
                "responderle y de reenviarte sus mensajes hasta que baje el ritmo."
            )
        elif motivo == "total" and limites.primer_aviso("total", limites.DIA):
            await telegram.alertar(
                f"⚠️ Sofía recibió más de {config.LIMITE_MENSAJES_DIA} mensajes en 24 horas y dejó de responder "
                "para cuidar el crédito. Sube LIMITE_MENSAJES_DIA en Railway si es tráfico real."
            )
    except Exception:
        logger.exception("Error al avisar del tope de mensajes")


async def procesar_mensaje(remitente: str, texto: str, wa_message_id: str) -> None:
    metricas.iniciar()
    try:
        try:
            es_nuevo = await store.registrar_entrante(remitente, texto, wa_message_id)
        except Exception:
            logger.exception("No se pudo guardar el mensaje %s", referencia(wa_message_id))
            await sin_guardado(remitente, texto)
            return
        if not es_nuevo:
            logger.info("Mensaje duplicado %s, se ignora", referencia(wa_message_id))
            return

        estado = await store.obtener_estado(remitente)
        if estado.get("desconocido"):
            await estado_desconocido(remitente, texto)
            return
        if estado["humano_activo"]:
            await reenviar_a_eduardo(remitente, texto)
            return
        if estado["venta_pausada"]:
            if texto.strip() == guardrails.PALABRA_ACEPTAR:
                await aceptar_aviso(remitente, texto, wa_message_id)
            else:
                await atender_crisis(remitente, nueva=False, texto=texto)
            return

        historial = await store.obtener_historial(remitente)
        recientes = guardrails.mensajes_para_crisis(historial, texto)
        # `actual=texto`: el último de la historia puede ser otro mensaje que llegó un instante después
        if await guardrails.detectar_crisis(recientes, actual=texto):
            logger.warning("Crisis detectada (msg %s)", referencia(wa_message_id))
            await atender_crisis(remitente, nueva=True)
            await metricas.registrar("crisis")
            return

        try:
            respuesta = await generar_validada(historial, remitente, await contexto_de(remitente, estado, historial))
        except Exception:
            logger.exception("Falló la respuesta de Sofía al mensaje %s", referencia(wa_message_id))
            await telegram.alertar(
                f"⚠️ No pude responderle a +{remitente}: falló la generación de la respuesta. "
                f"Escríbele tú.\n\n{_pie(remitente)}"
            )
            return
        cambio = await conversacion_cambio(remitente)
        if cambio:
            logger.warning(
                "La conversación cambió mientras se generaba la respuesta; no se envía (msg %s)",
                referencia(wa_message_id),
            )
            if cambio["humano_activo"]:
                await reenviar_a_eduardo(remitente, texto)
            else:
                await atender_crisis(remitente, nueva=False)
            return
        logger.info("Sofía responde %s", describir(respuesta.texto))
        # El aviso va en la primera respuesta de Sofía, no en la de crisis: ahí estorbaría. Quien empezó
        # con una crisis lo recibe cuando Sofía retome la conversación.
        aviso = "" if estado["aviso_privacidad"] else guardrails.AVISO_PRIVACIDAD.format(url=config.URL_PRIVACIDAD)
        try:
            await enviar(remitente, respuesta.texto, aviso)
            if aviso:
                await store.marcar_aviso_privacidad(remitente)
            await metricas.registrar(metricas.fase_de(respuesta.texto, respuesta.derivacion))
        finally:
            if respuesta.derivacion:
                await derivar(remitente, respuesta.derivacion["motivo"], respuesta.derivacion["resumen"], texto)
    except Exception:
        logger.exception("Error procesando mensaje %s", referencia(wa_message_id))


async def procesar_audio(remitente: str, mensaje: dict) -> None:
    """Nota de voz: se transcribe y sigue el mismo camino que un texto (crisis, modo humano, venta).
    Si no se puede transcribir, se le pide que lo escriba."""
    try:
        archivo, mime = await whatsapp.descargar_media(mensaje["media_id"])
        texto = await transcripcion.transcribir(archivo, mime)
    except Exception:
        logger.exception("No se pudo transcribir la nota de voz %s", referencia(mensaje["id"]))
        await audio_sin_transcribir(remitente, mensaje["id"])
        return
    await procesar_mensaje(remitente, guardrails.MARCA_AUDIO + texto, mensaje["id"])


async def audio_sin_transcribir(remitente: str, wa_message_id: str) -> None:
    try:
        if not await store.registrar_entrante(remitente, "[nota de voz sin transcribir]", wa_message_id):
            return
        estado = await store.obtener_estado(remitente)
        if estado["humano_activo"]:
            await telegram.alertar(
                f"🎤 +{remitente} mandó una nota de voz que no pude transcribir. Pídele que lo escriba.\n\n"
                f"{_pie(remitente)}"
            )
            return
        if estado["venta_pausada"]:
            # En una pausa por crisis no basta con pedir que lo escriban: no se sabe qué decía la nota
            await atender_crisis(remitente, nueva=False)
            return
        await enviar(remitente, guardrails.RESPUESTA_AUDIO_FALLIDO)
    except Exception:
        logger.exception("Error avisando de la nota de voz sin transcribir %s", referencia(wa_message_id))


async def procesar_media(remitente: str, mensaje: dict) -> None:
    """Imagen o documento: casi siempre un comprobante. Se reenvía a Eduardo y se deriva."""
    tipo = "imagen" if mensaje["type"] == "image" else "documento"
    contenido = f"[{tipo}] {mensaje['caption']}" if mensaje["caption"] else f"[{tipo}]"
    try:
        if not await store.registrar_entrante(remitente, contenido, mensaje["id"]):
            logger.info("Mensaje duplicado %s, se ignora", referencia(mensaje["id"]))
            return

        nota = f"{mensaje['caption']}\n" if mensaje["caption"] else ""
        leyenda = f"🧾 +{remitente} envió un {tipo} (posible comprobante)\n{nota}{_pie(remitente)}"
        botones = comandos.botones_comprobante(remitente)
        try:
            archivo, mime = await whatsapp.descargar_media(mensaje["media_id"])
            extension = mime.split("/")[-1].split(";")[0]
            llego = await telegram.enviar_archivo(archivo, f"{tipo}_{remitente}.{extension}", mime, leyenda, botones)
        except Exception:
            logger.exception("No se pudo descargar el %s %s", tipo, referencia(mensaje["id"]))
            await telegram.alertar(leyenda + "\n⚠️ No se pudo descargar el archivo de WhatsApp.", botones)
        else:
            if not llego:
                await telegram.alertar(
                    leyenda + "\n⚠️ Telegram no recibió el archivo: revísalo en WhatsApp o pídele que lo reenvíe.",
                    botones,
                )

        estado = await store.obtener_estado(remitente)
        # El pie de una imagen o un documento también puede ser una crisis: antes no se revisaba
        crisis = bool(mensaje["caption"]) and await guardrails.detectar_crisis([mensaje["caption"]])
        if estado["humano_activo"]:
            if crisis:
                await reenviar_a_eduardo(remitente, mensaje["caption"])
        elif crisis:
            await atender_crisis(remitente, nueva=True)
        elif estado["venta_pausada"]:
            await atender_crisis(remitente, nueva=False)  # el archivo ya está con Eduardo; la contención no se cambia
        else:
            await store.activar_humano(remitente, "comprobante")
            await enviar(remitente, guardrails.RESPUESTA_COMPROBANTE)
    except Exception:
        logger.exception("Error procesando %s %s", tipo, referencia(mensaje["id"]))


async def avisar_fallos(fallos: list[dict]) -> None:
    """Meta acepta un envío y minutos después puede marcarlo fallido; Eduardo debe enterarse."""
    for fallo in fallos:
        logger.warning("Meta no entregó un mensaje a %s (código %s)", referencia(fallo["destino"]), fallo["codigo"])
        if fallo["codigo"] == whatsapp.FUERA_DE_VENTANA:
            motivo = "su último mensaje fue hace más de 24 horas y WhatsApp solo permite plantillas aprobadas"
        else:
            motivo = f"código {fallo['codigo']}: {fallo['detalle']}"
        await telegram.alertar(f"⚠️ WhatsApp no entregó un mensaje a +{fallo['destino']}: {motivo}. Escríbele tú.")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/webhook")
def verificar(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge"),
):
    if mode == "subscribe" and token and token == config.VERIFY_TOKEN:
        logger.info("Webhook verificado")
        return PlainTextResponse(challenge)
    logger.warning("Verificación fallida")
    raise HTTPException(status_code=403, detail="Token inválido")


@app.post("/webhook")
async def recibir(request: Request, tareas: BackgroundTasks):
    cuerpo = await request.body()
    if not whatsapp.firma_valida(cuerpo, request.headers.get("X-Hub-Signature-256")):
        logger.warning("Firma inválida, se rechaza la petición")
        raise HTTPException(status_code=401, detail="Firma inválida")

    try:
        body = json.loads(cuerpo)
    except ValueError:
        raise HTTPException(status_code=400, detail="JSON inválido") from None

    fallos = whatsapp.extraer_fallos(body)
    if fallos:
        tareas.add_task(avisar_fallos, fallos)

    # Se responde 200 de inmediato; Meta reintenta si el webhook tarda. Una entrega puede traer varios mensajes.
    mensajes = whatsapp.extraer_mensajes(body)
    if not mensajes:
        return {"status": "fallo_reportado" if fallos else "ignorado"}

    recibidos = 0
    for mensaje in mensajes:
        motivo = limites.revisar(mensaje["from"])
        if motivo:
            logger.warning("Tope de mensajes (%s) para %s", motivo, referencia(mensaje["from"]))
            tareas.add_task(
                limite_excedido, mensaje["from"], mensaje.get("text") or mensaje.get("caption") or "", motivo
            )
            continue
        if mensaje["type"] in whatsapp.TIPOS_MEDIA and mensaje["media_id"]:
            logger.info("Archivo entrante %s (%s)", referencia(mensaje["id"]), mensaje["type"])
            tareas.add_task(procesar_media, mensaje["from"], mensaje)
        elif mensaje["type"] == whatsapp.AUDIO and mensaje["media_id"]:
            logger.info("Nota de voz entrante %s", referencia(mensaje["id"]))
            tareas.add_task(procesar_audio, mensaje["from"], mensaje)
        elif mensaje["type"] == "text" and mensaje["text"]:
            logger.info("Mensaje entrante %s %s", referencia(mensaje["id"]), describir(mensaje["text"]))
            tareas.add_task(procesar_mensaje, mensaje["from"], mensaje["text"], mensaje["id"])
        else:
            logger.info("Mensaje tipo %s no soportado aún (msg %s)", mensaje["type"], referencia(mensaje["id"]))
            continue
        recibidos += 1
    return {"status": "recibido" if recibidos else "ignorado"}


@app.post("/telegram/webhook")
async def recibir_telegram(request: Request, tareas: BackgroundTasks):
    if not telegram.secreto_valido(request.headers.get("X-Telegram-Bot-Api-Secret-Token")):
        logger.warning("Update de Telegram sin secreto válido, se rechaza")
        raise HTTPException(status_code=401, detail="Secreto inválido")
    try:
        update = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="JSON inválido") from None
    tareas.add_task(comandos.manejar, update)
    return {"ok": True}
