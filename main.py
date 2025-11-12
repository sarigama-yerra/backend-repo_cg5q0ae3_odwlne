import os
import random
import string
from typing import Optional, Dict, Any

import requests
from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Temp Mail Proxy API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAIL_TM_BASE = "https://api.mail.tm"


class NewAccountRequest(BaseModel):
    local: Optional[str] = None
    password: Optional[str] = None
    domain: Optional[str] = None


class TokenRequest(BaseModel):
    address: str
    password: str


class MailTmClient:
    def __init__(self, base_url: str = MAIL_TM_BASE):
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/ld+json"})

    def list_domains(self) -> list[dict]:
        url = f"{self.base}/domains"
        r = self.session.get(url, timeout=20)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Failed to fetch domains: {r.text}")
        data = r.json()
        return data.get("hydra:member", [])

    def create_account(self, address: str, password: str) -> dict:
        url = f"{self.base}/accounts"
        r = self.session.post(url, json={"address": address, "password": password}, timeout=20)
        if r.status_code not in (200, 201):
            # 409 means address exists
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return r.json()

    def get_token(self, address: str, password: str) -> dict:
        url = f"{self.base}/token"
        r = self.session.post(url, json={"address": address, "password": password}, timeout=20)
        if r.status_code != 200:
            raise HTTPException(status_code=401, detail=r.text)
        return r.json()

    def auth_headers(self, token: str) -> Dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Accept": "application/ld+json"}

    def me(self, token: str) -> dict:
        url = f"{self.base}/me"
        r = self.session.get(url, headers=self.auth_headers(token), timeout=20)
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return r.json()

    def messages(self, token: str, page: int = 1) -> dict:
        url = f"{self.base}/messages?page={page}"
        r = self.session.get(url, headers=self.auth_headers(token), timeout=20)
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return r.json()

    def message(self, token: str, message_id: str) -> dict:
        url = f"{self.base}/messages/{message_id}"
        r = self.session.get(url, headers=self.auth_headers(token), timeout=20)
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return r.json()


@app.get("/")
def read_root():
    return {"message": "Temp Mail Backend running"}


@app.get("/api/domains")
def get_domains():
    client = MailTmClient()
    domains = client.list_domains()
    return {"domains": domains}


def random_local_part(n: int = 10) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(n))


@app.post("/api/temp-mail/new")
def create_temp_mail(body: NewAccountRequest):
    client = MailTmClient()

    domains = client.list_domains()
    if not domains:
        raise HTTPException(status_code=502, detail="No domains available from mail.tm")

    domain = body.domain or domains[0].get("domain")
    if not domain:
        raise HTTPException(status_code=502, detail="Invalid domain from provider")

    # Prepare address and password
    local = body.local or random_local_part(10)
    address = f"{local}@{domain}"
    password = body.password or ("P@" + random_local_part(14))

    # Try a few times in case of collision
    last_err: Optional[Any] = None
    for _ in range(4):
        try:
            _ = client.create_account(address, password)
            token_payload = client.get_token(address, password)
            token = token_payload.get("token")
            me = client.me(token)
            return {
                "address": address,
                "password": password,
                "token": token,
                "account": me,
            }
        except HTTPException as e:
            last_err = e
            # If conflict, regenerate local part and retry
            if e.status_code == 409:
                local = random_local_part(10)
                address = f"{local}@{domain}"
                continue
            raise
    if isinstance(last_err, HTTPException):
        raise last_err
    raise HTTPException(status_code=500, detail="Failed to create account")


@app.post("/api/temp-mail/token")
def create_token(body: TokenRequest):
    client = MailTmClient()
    data = client.get_token(body.address, body.password)
    return data


@app.get("/api/temp-mail/messages")
def list_messages(
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
    page: int = 1,
):
    actual_token = token
    if not actual_token and authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            actual_token = parts[1]
    if not actual_token:
        raise HTTPException(status_code=400, detail="Missing token")
    client = MailTmClient()
    messages = client.messages(actual_token, page=page)
    return messages


@app.get("/api/temp-mail/messages/{message_id}")
def get_message(
    message_id: str,
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
):
    actual_token = token
    if not actual_token and authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            actual_token = parts[1]
    if not actual_token:
        raise HTTPException(status_code=400, detail="Missing token")
    client = MailTmClient()
    message = client.message(actual_token, message_id)
    return message


@app.get("/test")
def test_database():
    """Simple health check for backend"""
    return {"backend": "running"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
