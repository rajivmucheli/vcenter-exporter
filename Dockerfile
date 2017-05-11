FROM docker.io/python:2.7.13-alpine

MAINTAINER Thomas Graichen, thomas.graichen@sap.com

RUN pip install yamlconfig argparse pyVmomi prometheus-client

ADD vcenter-exporter.py /vcenter-exporter.py
ADD config.yaml /config.yaml
