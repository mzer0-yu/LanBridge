"""Tracked background publication after a local website save."""
from __future__ import annotations
from copy import deepcopy
import secrets
import threading
import time


class SitePublication:
    ACTIVE = {'queued', 'verification', 'publishing'}

    def __init__(self, service):
        self.service = service
        self.lock = threading.RLock()
        self.thread = None
        self.stopping = False

    def recover(self):
        previous = self.service.store.get('site_publication_job')
        if previous and previous.get('phase') in self.ACTIVE:
            self._update(phase='failed', message='上次后台发布被中断，配置已保存；请重试发布')

    def status(self):
        with self.lock:
            return deepcopy(self.service.store.get('site_publication_job'))

    def _update(self, **values):
        with self.lock:
            job = self.service.store.get('site_publication_job') or {}
            job.update(values, updated_at=time.time())
            self.service.store.set('site_publication_job', job)

    def submit(self, body=None, *, allow_static_target=True):
        with self.lock:
            if self.stopping:
                raise ValueError('平台正在停止，请稍后重试')
            if self.thread and self.thread.is_alive():
                raise ValueError('网站正在后台发布，请等待完成，勿重复提交')
            # No Cloudflare calls here. Credentials and passcodes never enter the job record.
            site = self.service.save_site(body, allow_static_target=allow_static_target) if body is not None else None
            job = {'id': secrets.token_hex(12), 'phase': 'queued', 'saved': True,
                   'site_id': site['id'] if site else '', 'created_at': time.time(),
                   'updated_at': time.time(), 'message': '配置已保存，正在等待后台发布'}
            self.service.store.set('site_publication_job', job)
            self.thread = threading.Thread(target=self._run, daemon=True, name='LanBridge-site-publication')
            try:
                self.thread.start()
            except RuntimeError:
                self._update(phase='failed', message='配置已保存，后台任务未能启动；请重试发布')
            current = self.status()
            return (site or {}) | {'saved': True, 'publication': {'status': 'failed' if current['phase'] == 'failed' else 'queued'}, 'site_publication': current}

    def _check_running(self):
        with self.lock:
            if self.stopping:
                raise RuntimeError('平台正在停止，后台发布未完成；请重试发布')

    def _run(self):
        try:
            with self.service.lock:
                self._check_running()
                sites = self.service.sites()
                if any(site['enabled'] and site['human_check'] for site in sites):
                    self._update(phase='verification', message='配置已保存，正在同步人类验证配置')
                    self.service.cf.create_widget(sites=sites)
                self._check_running()
                self._update(phase='publishing', message='配置已保存，正在发布 DNS 和网站路由并核验')
                desired = sorted(site['hostname'] for site in sites if site['enabled'])
                published = sorted(self.service.store.get('published_hosts', []))
                if desired != published or self.service.store.get('publication_error'):
                    preview = self.service.cf.plan()
                    self._check_running()
                    self.service.cf.apply(preview['revision'])
                with self.lock:
                    self._check_running()
                    self._update(phase='succeeded', message='配置已保存，后台发布已完成')
        except Exception as exc:
            message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else '后台发布未完成，请检查网络及配置后重试'
            if len(message) > 400 or message.startswith('{'):
                message = '后台发布未完成，请检查网络及配置后重试'
            self.service.store.set('publication_error', message)
            self.service.store.audit('publish_incomplete', {'automatic': True, 'reconcile_required': True})
            self._update(phase='failed', message='配置已保存，发布未完成：' + message)

    def stop(self):
        with self.lock:
            self.stopping = True
            thread = self.thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=90)
