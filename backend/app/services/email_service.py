import smtplib
import ssl
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.config import settings

logger = logging.getLogger(__name__)


def send_welcome_email(to_email: str) -> bool:
    """
    Отправляет приветственное письмо новому пользователю.
    Если SMTP не настроен — молча логирует и возвращает False (не падает).
    """
    if not settings.SMTP_USER or not settings.SMTP_PASS:
        logger.info(f"SMTP не настроен, пропускаем welcome email для {to_email}")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Добро пожаловать в QUASAR-X Repricer!"
        msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
        msg["To"] = to_email

        html = f"""
<!DOCTYPE html>
<html lang="ru">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0f0f1a;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td align="center" style="padding:40px 20px;">
        <table width="560" cellpadding="0" cellspacing="0"
               style="background:linear-gradient(135deg,#1a1a2e,#16213e);
                      border-radius:16px;border:1px solid #00f0ff22;
                      overflow:hidden;">
          <!-- Header -->
          <tr>
            <td align="center" style="padding:32px 40px 24px;">
              <div style="font-size:28px;font-weight:900;letter-spacing:2px;
                          background:linear-gradient(135deg,#00f0ff,#7c3aed);
                          -webkit-background-clip:text;-webkit-text-fill-color:transparent;
                          color:#00f0ff;">
                QUASAR-X
              </div>
              <div style="color:#6b7280;font-size:12px;letter-spacing:3px;margin-top:4px;">
                REPRICER
              </div>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:0 40px 32px;">
              <h1 style="color:#f9fafb;font-size:22px;margin:0 0 16px;">
                Добро пожаловать! 🚀
              </h1>
              <p style="color:#9ca3af;font-size:15px;line-height:1.6;margin:0 0 12px;">
                Ваш аккаунт успешно создан. Теперь вы можете подключить WB-аккаунт
                и настроить автоматическое управление ценами.
              </p>

              <!-- Steps -->
              <table width="100%" cellpadding="0" cellspacing="0"
                     style="background:#ffffff0a;border-radius:12px;margin:24px 0;">
                <tr>
                  <td style="padding:20px 24px;">
                    <p style="color:#00f0ff;font-size:13px;font-weight:700;
                               letter-spacing:1px;margin:0 0 14px;">
                      С ЧЕГО НАЧАТЬ:
                    </p>
                    <p style="color:#d1d5db;font-size:14px;margin:0 0 8px;">
                      1️⃣ &nbsp;Перейдите в раздел <b>Аккаунты</b> и добавьте WB API-токен
                    </p>
                    <p style="color:#d1d5db;font-size:14px;margin:0 0 8px;">
                      2️⃣ &nbsp;Добавьте <b>товары</b> (артикулы WB)
                    </p>
                    <p style="color:#d1d5db;font-size:14px;margin:0 0 8px;">
                      3️⃣ &nbsp;Настройте <b>стратегию</b> (РРЦ или конкурент)
                    </p>
                    <p style="color:#d1d5db;font-size:14px;margin:0;">
                      4️⃣ &nbsp;Включите репрайсер и следите за <b>историей цен</b>
                    </p>
                  </td>
                </tr>
              </table>

              <!-- CTA Button -->
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td align="center" style="padding:8px 0 24px;">
                    <a href="{settings.APP_URL}"
                       style="display:inline-block;
                              background:linear-gradient(135deg,#00f0ff,#7c3aed);
                              color:#fff;font-weight:700;font-size:15px;
                              text-decoration:none;padding:14px 40px;
                              border-radius:10px;letter-spacing:0.5px;">
                      Открыть репрайсер →
                    </a>
                  </td>
                </tr>
              </table>

              <p style="color:#4b5563;font-size:12px;text-align:center;margin:0;">
                Это письмо отправлено автоматически. Не отвечайте на него.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
        msg.attach(MIMEText(html, "html", "utf-8"))

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, context=context) as server:
            server.login(settings.SMTP_USER, settings.SMTP_PASS)
            server.sendmail(msg["From"], to_email, msg.as_string())

        logger.info(f"Welcome email отправлен на {to_email}")
        print(f"[EMAIL] ✅ Welcome email отправлен на {to_email}", flush=True)
        return True

    except Exception as e:
        logger.warning(f"Не удалось отправить email на {to_email}: {e}")
        print(f"[EMAIL] ❌ Ошибка отправки на {to_email}: {e}", flush=True)
        return False
