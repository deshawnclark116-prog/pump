"""Create an authorized Telethon StringSession ON YOUR OWN COMPUTER.

The session is as sensitive as a password. This local utility is never called
by GitHub Actions. Store the result in the repository's encrypted Actions secret
ATTENTION_TELEGRAM_SESSION; never commit, publish, or paste it into a chat.
"""
import asyncio
from getpass import getpass

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main():
    api_id=int(input("Telegram app API ID (from my.telegram.org): ").strip())
    api_hash=getpass("Telegram app API hash: ").strip()
    async with TelegramClient(StringSession(),api_id,api_hash) as client:
        await client.start()
        print("\nWARNING: Treat the following StringSession as a live account credential.")
        print("Paste it ONLY in GitHub Settings > Secrets and variables > Actions")
        print("as ATTENTION_TELEGRAM_SESSION. Never commit it to the repository.")
        print("\n"+client.session.save()+"\n")


if __name__=="__main__":
    asyncio.run(main())
