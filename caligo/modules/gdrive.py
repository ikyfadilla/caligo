import asyncio
import pickle
from typing import ClassVar, Dict

import pyrogram
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.discovery import Resource
from google.oauth2.credentials import Credentials
from motor.motor_asyncio import AsyncIOMotorDatabase
from oauthlib.oauth2.rfc6749.errors import InvalidGrantError
from pyrogram import filters

from .. import command, module, util


class GoogleDrive(module.Module):
    name: ClassVar[str] = "GoogleDrive"

    configs: Dict[str, str]
    creds: Credentials
    db: AsyncIOMotorDatabase
    lock: asyncio.Lock
    service: Resource

    async def on_load(self) -> None:
        self.configs = self.bot.getConfig.gdrive_secret
        self.creds = None
        self.lock = asyncio.Lock()

        self.db = self.bot.get_db("gdrive")

    async def on_started(self) -> None:
        data = await self.db.find_one({"_id": self.bot.uid})
        if not data:
            return

        self.creds = await util.run_sync(pickle.loads, data.get("creds"))
        # service will be overwrite if credentials is expired
        self.service = build(
            "drive",
            "v3",
            credentials=self.creds,
            cache_discovery=False
        )

    async def cmd_gdcheck(self, ctx: command.Context) -> None:
        await ctx.respond("You are all set.")
        return

    async def getAccessToken(self, message: pyrogram.types.Message) -> str:
        flow = InstalledAppFlow.from_client_config(
            self.configs, ["https://www.googleapis.com/auth/drive"],
            redirect_uri=self.configs["installed"].get("redirect_uris")[0]
        )
        auth_url, _ = flow.authorization_url(
            access_type="offline", prompt="consent"
        )

        await self.bot.respond(message, "Check your **Saved Message.**")
        try:
            res = await self.bot.client.ask(
                chat_id="me",
                text=f"Please visit the link:\n{auth_url}\n"
                "And reply the token here.\n**You have 60 seconds**.",
                filters=filters.me,
                timeout=60
            )
        except asyncio.exceptions.TimeoutError:
            await res.request.delete()
            return "⚠️ Timeout no token receive"

        await self.bot.respond(message, "Token received...")
        token = res.text

        await res.request.delete()
        await res.delete()

        try:
            await util.run_sync(flow.fetch_token, code=token)
        except InvalidGrantError:
            return (
                "⚠️ Error fetching token\n\n"
                "Refresh token is invalid, expired, revoked, "
                "or does not match the redirection URI."
            )

        self.creds = flow.credentials
        credential = await util.run_sync(pickle.dumps, self.creds)

        async with self.lock:
            await self.db.update_one(
                {"_id": self.bot.uid},
                {
                    "$set": {"creds": credential}
                },
                upsert=True
            )

        return "Credentials created."

    async def authorize(self, message: pyrogram.types.Message) -> None:
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.log.info("Refreshing credentials")
                await util.run_sync(self.creds.refresh, Request())

                credential = await util.run_sync(pickle.dumps, self.creds)
                async with self.lock:
                    await self.db.update_one(
                        {"_id": self.bot.uid},
                        {
                            "$set": {"creds": credential}
                        }
                    )
            else:
                await self.bot.respond(
                    message,
                    "Credential is empty, generating..."
                )
                await asyncio.sleep(1.5)  # give people time to read

                ret = await self.getAccessToken(message)

                await self.bot.respond(message, ret)
                if self.creds is None:
                    return False

            self.service = build(
                "drive",
                "v3",
                credentials=self.creds,
                cache_discovery=False
            )
