"""
Recordatorios por correo via la API REST de Resend. Se llama directo con
urllib (sin el SDK oficial) porque el pipeline de deploy solo empaqueta los
modulos propios de este repo -- boto3 lo da el runtime de Lambda nativamente,
pero cualquier paquete de terceros necesitaria un Lambda Layer nuevo.
"""
import json
import os
import urllib.request
import urllib.error

RESEND_API_URL = "https://api.resend.com/emails"
FROM_ADDRESS = "Kivo <onboarding@resend.dev>"


def send_email(subject, html):
    """Envia un correo. Si falta configuracion o Resend rechaza el envio,
    no truena el flujo que llamo esto -- un recordatorio que no salio no
    debe bloquear el avance de la simulacion ni una accion del chat."""
    api_key = os.environ.get("RESEND_API_KEY")
    to_address = os.environ.get("USER_EMAIL")
    if not api_key or not to_address:
        return

    payload = json.dumps({
        "from": FROM_ADDRESS,
        "to": [to_address],
        "subject": subject,
        "html": html,
    }).encode("utf-8")

    request = urllib.request.Request(
        RESEND_API_URL, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
            # Cloudflare (frente a api.resend.com) bloquea el User-Agent por
            # default de urllib ("Python-urllib/x.y") como firma de bot --
            # sin esto, todo envio truena con 403 antes de llegar a Resend.
            "User-Agent": "Mozilla/5.0 (compatible; Kivo-Backend/1.0)",
        },
    )
    try:
        urllib.request.urlopen(request, timeout=10)
    except urllib.error.URLError:
        pass
