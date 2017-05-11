#!/usr/bin/env python

# python interface to vmware performance metrics
from pyVmomi import vim, vmodl
# prometheus export functionality
from prometheus_client import start_http_server, Gauge
from pyVim.connect import SmartConnect, Disconnect
import atexit
import ssl
from yamlconfig import YamlConfig
import argparse
import re

# vcenter connection defaults
defaults = {
    'vcenter_ip': 'localhost',
    'vcenter_user': 'administrator@vsphere.local',
    'vcenter_password': 'password',
    'ignore_ssl': True
}


def main():

    # config file parsing
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-c", "--config", help="Specify config file", metavar="FILE")
    args, remaining_argv = parser.parse_known_args()
    config = YamlConfig(args.config, defaults)

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

    #try to filter out openstack generated vms
    pattern = re.compile("^name:")
    # create a mapping from performance stats to their counterIDs
    # counterInfo: [performance stat => counterId]
    # performance stat example: cpu.usagemhz.LATEST
    # counterId example: 6
    counterids = perfManager.QueryPerfCounterByLevel(level=4)

    # start up the http server to expose the prometheus metrics
    start_http_server(8000)

    print('INFO: list of all available metrics and their counterids')
    # loop over all counterids and build their full name and a dict relating it to the ids
    for c in counterids:
        fullName = c.groupInfo.key + "." + c.nameInfo.key + "." + c.rollupType
        print('INFO: ' + fullName + ': ' + str(c.key))
        counterInfo[fullName] = c.key

        # define a dict of gauges for the counter ids
        gauge['vcenter_' + fullName.replace('.', '_')] = Gauge(
            'vcenter_' + fullName.replace('.', '_'),
            'vcenter_' + fullName.replace('.', '_'),
            ['vmware_name', 'project_id', 'vcenter_name'])

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

        # create containerview to get a list of vmware machines
        containerView = content.viewManager.CreateContainerView(
            container, viewType, recursive)

        children = containerView.view
        count_vms = len(children)
        print('INFO: number of vms - ' + str(count_vms))

        # loop over all vmware machines
        for child in children:
            try:
                # only consider machines which have an annotation and are powered on
                if child.summary.config.annotation and child.summary.runtime.powerState == "poweredOn" and pattern.match(child.summary.config.annotation):
                    print('INFO: current vm processed - ' +
                          child.summary.config.name)

                    # split the multi-line annotation into a dict per property (name, project-id, ...)
                    annotation_lines = child.summary.config.annotation.split('\n')



                    # the filter is for filtering out empty lines

                    annotations = dict(
                        s.rsplit(':', 1)
                         for s in filter(None, annotation_lines))

                    # get a list of metricids for this vm in preparation for the stats query
                    metricIDs = [vim.PerformanceManager.MetricId(counterId=i, instance="*") for i in counterIDs]

                    # query spec for the metric stats query, we might get the interval from PerfProviderSummary later ...
                    spec = vim.PerformanceManager.QuerySpec(
                        maxSample=1,
                        entity=child,
                        metricId=metricIDs,
                        intervalId=20)

                    # get metric stats from vcenter
                    result = perfManager.QueryStats(querySpec=[spec])

                    # loop over the metrics
                    for val in result[0].value:
                        if val:
                            # send gauges to prometheus exporter: metricname and value with
                            # labels: vm name, project id and vcenter name
                            gauge['vcenter_' +
                                  counterInfo.keys()[counterInfo.values(
                                  ).index(val.id.counterId)].replace(
                                      '.', '_')].labels(
                                          annotations['name'],
                                          annotations['projectid'],
                                          config['main']['host'].replace(
                                              '.cloud.sap',
                                              '')).set(val.value[0])

            except vmodl.fault.ManagedObjectNotFound:
                print('INFO: a machine disappeared during processing')


if __name__ == "__main__":
    main()