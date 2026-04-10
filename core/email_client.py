"""IMAP fetch and SMTP send for email operations."""

import asyncio
import datetime
import logging
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib
from imap_tools import OR, A, MailBox, MailboxLoginError
from imap_tools.utils import clean_uids
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.config import (
    EMAIL_ACCOUNT,
    EMAIL_PASSWORD,
    IMAP_SERVER,
    KINDLE_EMAIL,
    NYT_SENDERS,
    SMTP_PORT,
    SMTP_SERVER,
    CacheDocument,
    _is_junk_section,
)
from core.extractor import (
    _split_newsletter_sections,
    extract_text_from_html,
)

logger = logging.getLogger(__name__)


def _fetch_emails_sync() -> tuple[list[CacheDocument], list[str], list[str], set[str]]:
    """Synchronously fetches newsletters via IMAP and extracts text + links."""
    cache_global: list[CacheDocument] = []
    processed: list[str] = []
    seen_urls: set[str] = set()
    global_urls_to_fetch: list[str] = []
    doc_counter = 0

    date_since = datetime.date.today() - datetime.timedelta(days=1)

    if not NYT_SENDERS:
        return [], [], [], set()

    sender_queries = [A(from_=sender) for sender in NYT_SENDERS]
    query = A(OR(*sender_queries), date_gte=date_since)

    try:
        with MailBox(IMAP_SERVER).login(EMAIL_ACCOUNT, EMAIL_PASSWORD) as mailbox:
            messages = list(mailbox.fetch(query, headers_only=False))
            logger.info("Found %d NYT newsletter(s). Processing…", len(messages))

            for msg in messages:
                html_body = msg.html or msg.text
                if html_body is None:
                    logger.warning(
                        "Email UID %s has no HTML or text body — marking for deletion.",
                        msg.uid,
                    )
                    processed.append(msg.uid)
                    continue

                text_md, urls_of_mail = extract_text_from_html(html_body, seen_urls)

                if text_md:
                    sections = _split_newsletter_sections(text_md)
                    for section in sections:
                        if _is_junk_section(section):
                            logger.debug(
                                "🗑️ Junk section dropped pre-cache (%d bytes).",
                                len(section),
                            )
                            continue

                        doc_counter += 1
                        cache_global.append(
                            CacheDocument(
                                id=f"doc_{doc_counter}",
                                source="newsletter",
                                text=section,
                            )
                        )

                if urls_of_mail:
                    global_urls_to_fetch.extend(urls_of_mail)

                if text_md or urls_of_mail:
                    processed.append(msg.uid)
    except MailboxLoginError as e:
        logger.error(
            "IMAP authentication failed — check EMAIL_ACCOUNT and EMAIL_PASSWORD: %s", e
        )
        raise
    except Exception as e:
        logger.error("IMAP error during extraction: %s", e)
        return [], [], [], set()

    return cache_global, processed, global_urls_to_fetch, seen_urls


async def send_to_kindle(summary_text: str, date_str: str) -> bool:
    """Sends the summary as an HTML attachment to the Kindle email address."""
    msg = MIMEMultipart()
    msg["From"] = EMAIL_ACCOUNT
    msg["To"] = KINDLE_EMAIL
    msg["Subject"] = f"Newsletter Summary - {date_str}"

    msg.attach(MIMEText("Please find the newsletter summary attached.", "plain"))

    filename = f"Newsletter_Summary_{date_str.replace('/', '-')}.html"
    attachment = MIMEApplication(summary_text.encode("utf-8"), _subtype="html")
    attachment.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(attachment)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=10),
        retry=retry_if_exception_type(
            (aiosmtplib.SMTPException, ConnectionError, TimeoutError, OSError),
        ),
        reraise=True,
    )
    async def _smtp_send() -> None:
        await aiosmtplib.send(
            msg,
            hostname=SMTP_SERVER,
            port=SMTP_PORT,
            username=EMAIL_ACCOUNT,
            password=EMAIL_PASSWORD,
            use_tls=True,
            start_tls=False,
        )

    try:
        await _smtp_send()
        logger.info("Successfully sent summary to Kindle: %s", KINDLE_EMAIL)
        return True
    except (aiosmtplib.SMTPException, ConnectionError, TimeoutError, OSError) as e:
        logger.error("Error sending email to Kindle after retries: %s", e)
        return False


def _cleanup_emails_sync(email_uids: list[str]) -> None:
    """Moves processed emails to Trash and expunges them."""
    if not email_uids:
        return
    try:
        with MailBox(IMAP_SERVER).login(EMAIL_ACCOUNT, EMAIL_PASSWORD) as mailbox:
            if "gmail.com" in IMAP_SERVER.lower():
                # Gmail: X-GM-LABELS moves to Trash. Do NOT call mailbox.delete()
                # afterwards — permanently deletes instead of keeping in Trash.
                uid_str = clean_uids(email_uids)
                mailbox.client.uid("STORE", uid_str, "+X-GM-LABELS", "(\\Trash)")
            else:
                # Other IMAP providers: standard \Deleted flag + expunge
                mailbox.delete(email_uids)
            mailbox.client.expunge()
            logger.info("Processed %d email(s) moved to Trash.", len(email_uids))
    except Exception as e:
        logger.error("IMAP error during cleanup: %s", e)


async def cleanup_emails(email_uids: list[str]) -> None:
    """Moves processed emails asynchronously to Trash and expunges (batch)."""
    await asyncio.to_thread(_cleanup_emails_sync, email_uids)
