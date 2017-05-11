# vcenter-exporter
Prometheus vcenter exporter


This is a Prometheus exporter, which collects virtual machines, performance metrics from vsphere api. 
The metric names, which can be defined in the config file are a construct of different fields in counterids.
The name is a construction of: counterid.groupInfo.key + "." + counterid.nameInfo.key + "." + counterid.rollupType:
an example:

```
cpu.usage.average
disk.usage.average
mem.usage.average
mem.overhead.average
mem.swapinRate.average
mem.swapoutRate.average
...
```

### Openstack specific notes:
The metrics are only collected for vms, which are in state "poweredOn" and have an annotation field (child.summary.config.annotation), which starts with "name:" otherwise
we cannot attach the openstack specific metadata to the collected metrics, which are definied in the annotation field of each vm like Openstack vm name and Openstack project id.



## Installation


To build the Docker container:

```bash
docker build .
```

## Usage

Command-line option:

```
NAME:
  vcenter-exporter.py - Prometheus vcenter exporter for vm metrics

USAGE:
  python vcenter-export.py
  --config, -c config.yaml
```
