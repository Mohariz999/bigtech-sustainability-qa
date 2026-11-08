"""Offline test boundary: a regression must never spend API credits or download models."""
import socket

import pytest


@pytest.fixture(autouse=True)
def offline_network_boundary(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('Network access is forbidden in the offline test suite')

    monkeypatch.setattr(socket, 'create_connection', refuse)
    monkeypatch.setattr(socket.socket, 'connect', refuse)
    monkeypatch.setattr(socket.socket, 'connect_ex', refuse)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
