"""Use the official Alibaba Cloud CLI OAuth/STS flow in an isolated profile."""
from __future__ import annotations
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from .store import protect_directory

PROFILE = "lanbridge"
FIELDS = {"name", "mode", "access_key_id", "access_key_secret", "sts_token", "sts_expiration", "oauth_access_token", "oauth_refresh_token", "oauth_access_token_expire", "oauth_refresh_token_expire", "oauth_site_type", "region_id", "output_format", "language"}


class AliyunOAuth:
    def __init__(self, store):
        self.store, self.lock = store, threading.RLock()

    def command(self):
        bundled = self.store.root.parent / "bin" / "aliyun.exe"
        found = str(bundled) if bundled.is_file() else shutil.which("aliyun")
        if not found:
            raise ValueError("请先安装阿里云官方 CLI 3.3.0 或更新版本，或将 aliyun.exe 放入项目 bin 目录，再点击浏览器授权")
        return found

    @staticmethod
    def environment():
        env = {k:v for k,v in os.environ.items() if not k.upper().startswith(('ALIBABA_CLOUD_', 'ALIYUN_')) and k.upper() not in {'DEBUG', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'}}
        env['ALIBABA_CLOUD_DISABLE_EXTERNAL_PROCESS'] = 'true'
        return env

    @contextmanager
    def configuration(self, profile=None):
        root = self.store.root / 'aliyun-oauth'
        protect_directory(root)
        with tempfile.TemporaryDirectory(prefix='session-', dir=root) as directory:
            config = Path(directory) / '.aliyun' / 'config.json'
            config.parent.mkdir()
            initial = profile or dict(name=PROFILE, mode='OAuth', oauth_site_type='CN', region_id='cn-hangzhou', language='zh', output_format='json')
            config.write_text(json.dumps({'current':PROFILE,'profiles':[initial]}),encoding='utf-8')
            yield config

    def run(self, config, args, *, authorize=False):
        command = [self.command(), *args, '--profile', PROFILE, '--config-path', str(config)]
        # CLI credential refresh uses its default home path in some versions.
        # Keep that path identical to our custom config, never the user's profile.
        env = self.environment()
        home = str(config.parent.parent)
        env.update(HOME=home, USERPROFILE=home, HOMEDRIVE='', HOMEPATH=home)
        try:
            result = subprocess.run(command, input=b'\n\n' if authorize else None, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=self.store.root, timeout=180 if authorize else 30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if result.returncode:
                raise ValueError('阿里云浏览器授权或身份核验未完成，请检查 official-cli 身份分配、RAM 权限及网络后重试')
            if len(result.stdout)>1024*1024:
                raise ValueError('阿里云响应过大')
            return result.stdout.decode('utf-8')
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            raise ValueError('阿里云授权未完成或已超时，请检查网络和浏览器后重试') from None

    @staticmethod
    def read_profile(config):
        raw = json.loads(config.read_text(encoding='utf-8-sig'))
        profiles = raw.get('profiles', []) if isinstance(raw,dict) else []
        if not isinstance(profiles,list):
            raise ValueError('阿里云 OAuth 配置不完整')
        profile = next((p for p in profiles if isinstance(p,dict) and p.get('name')==PROFILE), None)
        if not profile or profile.get('mode')!='OAuth' or profile.get('oauth_site_type')!='CN':
            raise ValueError('阿里云 OAuth 配置不完整')
        if not all(isinstance(profile.get(k),str) and profile[k] and len(profile[k])<=16384 for k in ('access_key_id','access_key_secret','sts_token','oauth_refresh_token')):
            raise ValueError('阿里云 OAuth 临时凭据不完整')
        if type(profile.get('sts_expiration')) not in (int,float) or profile['sts_expiration']<=time.time()+30:
            raise ValueError('阿里云临时授权未能续期，请重新浏览器授权')
        return {k:v for k,v in profile.items() if k in FIELDS}

    def authorize(self):
        with self.lock, self.configuration() as config:
            self.run(config,['configure','--mode','OAuth'],authorize=True)
            identity = json.loads(self.run(config,['sts','GetCallerIdentity']))
            if not isinstance(identity,dict) or not isinstance(identity.get('AccountId'),str) or not identity['AccountId']:
                raise ValueError('阿里云身份核验失败')
            return self.read_profile(config), {k:identity.get(k) for k in ('AccountId','Arn','UserId')}

    def credentials(self):
        with self.lock:
            raw = self.store.secret('aliyun_oauth_profile')
            if not raw:
                raise ValueError('请先完成阿里云浏览器授权')
            profile = json.loads(raw)
            if not isinstance(profile,dict) or profile.get('mode')!='OAuth' or profile.get('oauth_site_type')!='CN':
                raise ValueError('阿里云 OAuth 配置不完整，请重新浏览器授权')
            profile = {k:v for k,v in profile.items() if k in FIELDS}
            if profile.get('sts_expiration',0)<=time.time()+90:
                profile['sts_expiration'] = 0
                with self.configuration(profile) as config:
                    identity = json.loads(self.run(config,['sts','GetCallerIdentity']))
                    expected = self.store.get('aliyun_oauth_identity')
                    if expected and (not isinstance(identity,dict) or identity.get('AccountId')!=expected.get('AccountId')):
                        raise ValueError('阿里云续期身份发生变化，请重新浏览器授权')
                    profile = self.read_profile(config)
                    self.store.set_secret('aliyun_oauth_profile',json.dumps(profile))
            return profile['access_key_id'],profile['access_key_secret'],profile['sts_token']
