"""模块层 — 认证与账号。

路由前缀 /api/auth
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import (authenticate, create_token, create_user, current_user)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class RegisterIn(BaseModel):
    username: str = Field(min_length=3)
    password: str = Field(min_length=6)
    email: str | None = None
    full_name: str | None = None
    company: str | None = None


@router.post("/register")
def api_register(body: RegisterIn):
    user = create_user(body.username, body.password, body.email,
                       body.full_name, body.company)
    return {"token": create_token(user["id"], user["username"], user["role"]),
            "user": user}


@router.post("/login")
def api_login(body: LoginIn):
    user = authenticate(body.username, body.password)
    if not user:
        raise HTTPException(401, "用户名或密码错误")
    return {"token": create_token(user["id"], user["username"], user["role"]),
            "user": {k: user[k] for k in
                     ("id", "username", "email", "full_name", "company", "role")}}


@router.get("/me")
def api_me(u: dict = Depends(current_user)):
    return u
