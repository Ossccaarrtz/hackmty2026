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


def wrap(body_html):
    """Envuelve el contenido de un correo en la plantilla visual de Kivo --
    tono directo, con personalidad, pensado para conectar con estudiantes
    (estilo Duolingo: cercano, un poco travieso, nunca acartonado)."""
    return f"""
<div style="font-family:'DM Sans',Arial,sans-serif;max-width:480px;margin:0 auto;background:#eef1f5;padding:24px 16px;">
  <div style="background:#ffffff;border-radius:20px;overflow:hidden;box-shadow:0 4px 16px rgba(15,35,60,.08);">
    <div style="background:#00304f;padding:18px 28px;">
      <span style="color:#ffffff;font-size:22px;font-weight:700;letter-spacing:-.5px;">kivo</span>
    </div>
    <div style="padding:28px;color:#14191c;font-size:15px;line-height:1.6;">
      {body_html}
    </div>
  </div>
  <p style="text-align:center;color:#93a1b8;font-size:12px;margin-top:16px;">Kivo · tu copiloto financiero 💙</p>
</div>
""".strip()


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
