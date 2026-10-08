from __future__ import annotations
import ipaddress
import re
from urllib.parse import urlsplit
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator


class Site(BaseModel):
    id: str = ""
    name: str = Field(min_length=1, max_length=80)
    hostname: str
    zone_id: str = ""
    origin: str
    target: Literal["website", "lanbridge"] = "website"
    enabled: bool = True
    paused: bool = False
    protocols: list[Literal["http", "websocket"]] = Field(default_factory=lambda: ["http", "websocket"], min_length=1)
    human_check: bool = True
    passcode_required: bool = False
    allowed_countries: list[str] = Field(default_factory=list)
    allowed_ips: list[str] = Field(default_factory=list)
    requests_per_minute: int = Field(default=180, ge=10, le=10000)
    session_minutes: int = Field(default=60, ge=5, le=1440)
    human_remember_days: int = Field(default=1, ge=0, le=30)
    policy_version: str = ""

    @field_validator("hostname")
    @classmethod
    def host(cls, v):
        v = v.strip().lower().rstrip(".")
        if len(v) > 253 or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", v):
            raise ValueError("请输入完整公网域名，例如 app.example.com")
        return v

    @field_validator("zone_id")
    @classmethod
    def zone_identity(cls, v):
        v = v.strip().lower()
        if v and not re.fullmatch("[a-f0-9]{32}", v):
            raise ValueError("Zone ID 必须为 32 位十六进制")
        return v

    @field_validator("origin")
    @classmethod
    def source(cls, v):
        u = urlsplit(v.strip())
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ("", "/"):
            raise ValueError("源站仅支持 http(s)://主机:端口；不含路径、账号或查询参数")
        try:
            port = u.port
        except ValueError:
            raise ValueError("端口无效")
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("端口无效")
        return v.strip().rstrip("/")

    @field_validator("allowed_countries")
    @classmethod
    def countries(cls, values):
        result = sorted(set(v.strip().upper() for v in values if v.strip()))
        if any(not re.fullmatch("[A-Z]{2}", v) for v in result):
            raise ValueError("国家使用两位代码，例如 CN、US、HK")
        return result

    @field_validator("allowed_ips")
    @classmethod
    def networks(cls, values):
        return [str(ipaddress.ip_network(v.strip(), strict=False)) for v in values if v.strip()]


class Zone(BaseModel):
    zone_id: str
    zone_name: str

    @field_validator("zone_id")
    @classmethod
    def identity(cls, v):
        value = Site.zone_identity(v)
        if not value:
            raise ValueError("域名必须填写 Zone ID")
        return value

    @field_validator("zone_name")
    @classmethod
    def name(cls, v):
        return Site.host(v)


class Settings(BaseModel):
    account_id: str = ""
    zone_id: str = ""
    zone_name: str = ""
    zones: list[Zone] = Field(default_factory=list, max_length=100)
    tunnel_id: str = ""
    tunnel_name: str = "LanBridge"
    cloudflared_path: str = ""
    turnstile_sitekey: str = ""
    gateway_port: int = Field(default=8891, ge=1024, le=65535)
    admin_port: int = Field(default=8890, ge=1024, le=65535)

    @field_validator("account_id", "zone_id")
    @classmethod
    def cf_id(cls, v):
        v = v.strip().lower()
        if v and not re.fullmatch("[a-fA-F0-9]{32}", v):
            raise ValueError("Cloudflare Account/Zone ID 必须为 32 位十六进制")
        return v

    @field_validator("tunnel_id")
    @classmethod
    def tunnel(cls, v):
        v = v.strip().lower()
        if v and not re.fullmatch("[a-fA-F0-9-]{36}", v):
            raise ValueError("Tunnel ID 格式无效")
        return v

    @field_validator("zone_name")
    @classmethod
    def zone(cls, v):
        return Site.host(v) if v else ""

    @model_validator(mode="after")
    def ports(self):
        if self.admin_port == self.gateway_port:
            raise ValueError("管理台与网关端口必须不同")
        if self.zone_id and self.zone_name:
            primary = Zone(zone_id=self.zone_id, zone_name=self.zone_name)
            if not any(z.zone_id == primary.zone_id for z in self.zones):
                self.zones.insert(0, primary)
            elif any(z.zone_id == primary.zone_id and z.zone_name != primary.zone_name for z in self.zones):
                raise ValueError("默认域名与域名列表不一致")
        if len(self.zones) > 100:
            raise ValueError("最多接入 100 个域名（含默认域名）")
        if len({z.zone_id for z in self.zones}) != len(self.zones) or len({z.zone_name for z in self.zones}) != len(self.zones):
            raise ValueError("域名或 Zone ID 不能重复")
        return self
