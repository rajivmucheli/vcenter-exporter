#!/usr/bin/env python

# python interface to vmware performance metrics
from pyVmomi import vim, vmodl
# prometheus export functionality
from prometheus_client import start_http_server, Gauge
from pyVim.connect import SmartConnect, Disconnect
import atexit
import ssl
import sys
from yamlconfig import YamlConfig
import argparse
import re
import logging
import time
import datetime

# vcenter connection defaults
defaults = {
    'vcenter_ip': 'localhost',
    'vcenter_user': 'administrator@vsphere.local',
    'vcenter_password': 'password',
    'ignore_ssl': True
}

logger = logging.getLogger()


def main():

    # config file parsing
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config", help="Specify config file", metavar="FILE")
    args, remaining_argv = parser.parse_known_args()
    config = YamlConfig(args.config, defaults)

    # set default log level if not defined in config file
    if config.get('main').get('log'):
      logger.setLevel(logging.getLevelName(config.get('main').get('log').upper()))
    else:
      logger.setLevel('INFO')
    FORMAT = '[%(asctime)s] [%(levelname)s] %(message)s'
    logging.basicConfig(stream=sys.stdout, format=FORMAT)


    # check for insecure ssl option
    si = None
    context = None
    if config.get('main').get('ignore_ssl') and \
       hasattr(ssl, "_create_unverified_context"):
        context = ssl._create_unverified_context()

    # connect to vcenter
    si = SmartConnect(
         host=config.get('main').get('host'),
         user=config.get('main').get('user'),
         pwd=config.get('main').get('password'),
         port=int(config.get('main').get('port')),
         sslContext=context)
    atexit.register(Disconnect, si)

    if not si:
        raise SystemExit("Unable to connect to host with supplied info.")

    content = si.RetrieveContent()
    perfManager = content.perfManager

    # create a list of vim.VirtualMachine objects so that we can query them for statistics
    container = content.rootFolder
    viewType = [vim.VirtualMachine]
    recursive = True

    counterInfo = {}
    gauge = {}

    # try to filter out openstack generated vms
    pattern = re.compile("^name:")

    # compile a regex for stripping out not required parts of hostnames etc. to have shorter label names (for better grafana display)
    shorter_names_regex = re.compile("\.cc\..*\.cloud\.sap")

    # create a mapping from performance stats to their counterIDs
    # counterInfo: [performance stat => counterId]
    # performance stat example: cpu.usagemhz.LATEST
    # counterId example: 6
    counterids = perfManager.QueryPerfCounterByLevel(level=4)

    # start up the http server to expose the prometheus metrics
    start_http_server(8000)

    logging.debug('list of all available metrics and their counterids')
    # loop over all counterids and build their full name and a dict relating it to the ids
    for c in counterids:
        fullName = c.groupInfo.key + "." + c.nameInfo.key + "." + c.rollupType
        logging.debug(fullName + ': ' + str(c.key))
        counterInfo[fullName] = c.key

        # define a dict of gauges for the counter ids
        gauge['vcenter_' + fullName.replace('.', '_')] = Gauge(
            'vcenter_' + fullName.replace('.', '_'),
            'vcenter_' + fullName.replace('.', '_'),
            ['vmware_name', 'project_id', 'vcenter_name', 'vcenter_node',
             'instance_uuid', 'metric_detail'])

    # in case we have a set of metric to handle use those, otherwise use all we can get
    selected_metrics = config.get('main').get('vm_metrics')
    if selected_metrics:
        counterIDs = [
            counterInfo[i] for i in selected_metrics if i in counterInfo
        ]
    else:
        counterIDs = [i.key for i in counterids]



    # infinite loop for getting the metrics
    while True:

        # get all the data regarding vcenter hosts
        hostView = content.viewManager.CreateContainerView(container,
                                                        [vim.HostSystem],
                                                        recursive)

        hostssystems = hostView.view

        # build a dict to lookup the hostname by its id later
        hostsystemsdict = {}
        for host in hostssystems:
            hostname = host.name
            hostsystemsdict[host] = hostname
        logging.debug('list of all available vcenter nodes and their internal id')
        logging.debug(hostsystemsdict)

        # create containerview to get a list of vmware machines
        containerView = content.viewManager.CreateContainerView(
            container, viewType, recursive)

        children = containerView.view
        count_vms = len(children)
        logging.info('number of vms - ' + str(count_vms))

        # loop over all vmware machines
        for child in children:
            try:
                # only consider machines which have an annotation and are powered on
                if child.summary.runtime.powerState == "poweredOn" and pattern.match(child.summary.config.annotation):
                    logging.debug('current vm processed - ' +
                          child.summary.config.name)

                    logging.debug('==> running on vcenter node: ' + hostsystemsdict[child.summary.runtime.host])

                    # split the multi-line annotation into a dict per property (name, project-id, ...)
                    annotation_lines = child.summary.config.annotation.split('\n')

                    # the filter is for filtering out empty lines

                    annotations = dict(
                        s.rsplit(':', 1)
                         for s in filter(None, annotation_lines))

                    # get a list of metricids for this vm in preparation for the stats query
                    metricIDs = [vim.PerformanceManager.MetricId(counterId=i, instance="*") for i in counterIDs]

                    # query spec for the metric stats query, we might get the interval from PerfProviderSummary later ...
                    logging.debug('==> vim.PerformanceManager.QuerySpec start: %s' % datetime.datetime.now())
                    spec = vim.PerformanceManager.QuerySpec(
                        maxSample=1,
                        entity=child,
                        metricId=metricIDs,
                        intervalId=20)
                    logging.debug('==> vim.PerformanceManager.QuerySpec end: %s' % datetime.datetime.now())

                    # get metric stats from vcenter
                    logging.debug('==> perfManager.QueryStats start: %s' % datetime.datetime.now())
                    result = perfManager.QueryStats(querySpec=[spec])
                    logging.debug('==> perfManager.QueryStats end: %s' % datetime.datetime.now())

                    # evaluate those outside of the values loop, as they are to
                    # expensive to evaluate each time
                    instance_uuid = child.summary.config.instanceUuid
                    runtime_host = child.summary.runtime.host

                    # loop over the metrics
                    logging.debug('==> gauge loop start: %s' % datetime.datetime.now())
                    for val in result[0].value:
                        # send gauges to prometheus exporter: metricname and value with
                        # labels: vm name, project id, vcenter name, vcneter
                        # node and instance uuid - we update the gauge only if
                        # the value is not -1 which means the vcenter has no
                        # value
                        if val.value[0] != -1:
                            if val.id.instance == '':
                                metric_detail = 'none'
                            else:
                                metric_detail = val.id.instance
                            gauge['vcenter_' +
                                  counterInfo.keys()[counterInfo.values(
                                  ).index(val.id.counterId)].replace(
                                      '.', '_')].labels(
                                          annotations['name'],
                                          annotations['projectid'],
                                          shorter_names_regex.sub('',config['main']['host']),
                                          shorter_names_regex.sub('',hostsystemsdict[runtime_host]),
                                          instance_uuid,
                                          metric_detail
                            ).set(val.value[0])
                    logging.debug('==> gauge loop end: %s' % datetime.datetime.now())

            except vmodl.fault.ManagedObjectNotFound:
                logging.info('a machine disappeared during processing')
            except IndexError:
                logging.info('a machine disappeared during processing')

        time.sleep(config.get('main').get('interval'))


if __name__ == "__main__":
    main()
