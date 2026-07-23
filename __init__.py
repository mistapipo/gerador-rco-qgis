"""Ponto de entrada do complemento Gerador de RCO."""


def classFactory(iface):
    from .plugin import GeradorRCOPlugin

    return GeradorRCOPlugin(iface)
