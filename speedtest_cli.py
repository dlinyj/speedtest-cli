#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright 2013 Matt Martz
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

__version__ = '1.0.0'

# Some global variables we use
source = None
shutdown_event = None

import math
import time
import os
import sys
import threading
import re
import signal
import socket

# Used for bound_interface
socket_socket = socket.socket

try:
    import xml.etree.cElementTree as ET
except ImportError:
    try:
        import xml.etree.ElementTree as ET
    except ImportError:
        from xml.dom import minidom as DOM
        ET = None

# Begin import game to handle Python 2 and Python 3
try:
    from urllib2 import urlopen, Request, HTTPError, URLError
except ImportError:
    from urllib.request import urlopen, Request, HTTPError, URLError

try:
    from Queue import Queue
except ImportError:
    from queue import Queue

try:
    from urlparse import urlparse
except ImportError:
    from urllib.parse import urlparse

try:
    from urlparse import parse_qs
except ImportError:
    try:
        from urllib.parse import parse_qs
    except ImportError:
        from cgi import parse_qs

try:
    from hashlib import md5
except ImportError:
    from md5 import md5

try:
    from argparse import ArgumentParser as ArgParser
    PARSER_TYPE_INT = int
except ImportError:
    from optparse import OptionParser as ArgParser
    PARSER_TYPE_INT = 'int'

try:
    import builtins
except ImportError:
    def print_(*args, **kwargs):
        """The new-style print function taken from
        https://pypi.python.org/pypi/six/

        """
        fp = kwargs.pop("file", sys.stdout)
        if fp is None:
            return

        def write(data):
            if not isinstance(data, basestring):
                data = str(data)
            fp.write(data)

        want_unicode = False
        sep = kwargs.pop("sep", None)
        if sep is not None:
            if isinstance(sep, unicode):
                want_unicode = True
            elif not isinstance(sep, str):
                raise TypeError("sep must be None or a string")
        end = kwargs.pop("end", None)
        if end is not None:
            if isinstance(end, unicode):
                want_unicode = True
            elif not isinstance(end, str):
                raise TypeError("end must be None or a string")
        if kwargs:
            raise TypeError("invalid keyword arguments to print()")
        if not want_unicode:
            for arg in args:
                if isinstance(arg, unicode):
                    want_unicode = True
                    break
        if want_unicode:
            newline = unicode("\n")
            space = unicode(" ")
        else:
            newline = "\n"
            space = " "
        if sep is None:
            sep = space
        if end is None:
            end = newline
        for i, arg in enumerate(args):
            if i:
                write(sep)
            write(arg)
        write(end)
else:
    print_ = getattr(builtins, 'print')
    del builtins


def bound_socket(*args, **kwargs):
    """Bind socket to a specified source IP address"""

    global source
    sock = socket_socket(*args, **kwargs)
    sock.bind((source, 0))
    return sock


def distance(origin, destination):
    """Determine distance between 2 sets of [lat,lon] in km"""

    lat1, lon1 = origin
    lat2, lon2 = destination
    radius = 6371  # km

    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) * math.sin(dlat / 2) + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2)) * math.sin(dlon / 2)
         * math.sin(dlon / 2))
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    d = radius * c

    return d


def get_attributes_by_tag_name(dom, tagName):
    """Retrieve an attribute from an XML document and return it in a
    consistent format

    Only used with xml.dom.minidom, which is likely only to be used
    with python versions older than 2.5
    """
    elem = dom.getElementsByTagName(tagName)[0]
    return dict(list(elem.attributes.items()))


def print_dots(current, total):
    sys.stdout.write('.')
    if current + 1 == total:
        sys.stdout.write('\n')
    sys.stdout.flush()


class Downloader(threading.Thread):
    """Thread class for retrieving a URL"""

    def __init__(self, url, start):
        self.url = url
        self.result = None
        self.starttime = start
        threading.Thread.__init__(self)

    def run(self):
        self.result = [0]
        try:
            if (time.time() - self.starttime) <= 10:
                f = urlopen(self.url)
                while (1 and not shutdown_event.isSet() and
                        (time.time() - self.starttime) <= 10):
                    self.result.append(len(f.read(10240)))
                    if self.result[-1] == 0:
                        break
                f.close()
        except:
            pass


class Uploader(threading.Thread):
    """Thread class for uploading to a URL"""

    def __init__(self, url, start, size):
        self.url = url
        chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        data = chars * (int(round(int(size) / 36.0)))
        self.data = ('content1=%s' % data[0:int(size) - 9]).encode()
        del data
        self.result = None
        self.starttime = start
        threading.Thread.__init__(self)

    def run(self):
        try:
            if ((time.time() - self.starttime) <= 10 and
                    not shutdown_event.isSet()):
                f = urlopen(self.url, self.data)
                f.read(11)
                f.close()
                self.result = len(self.data)
            else:
                self.result = 0
        except:
            self.result = 0


class SpeedtestException(Exception):
    """Base exception for this module"""
    pass


class ConfigRetrievalError(SpeedtestException):
    """Could not retrieve config.php"""
    pass


class ServersRetrievalError(SpeedtestException):
    """Could not retrieve speedtest-servers.php"""
    pass


class InvalidServerIDType(SpeedtestException):
    """Server ID used for filtering was not an integer"""
    pass


class NoMatchedServers(SpeedtestException):
    """No servers matched when filtering"""
    pass


class SpeedtestMiniConnectFailure(SpeedtestException):
    """Could not connect to the provided speedtest mini server"""
    pass


class InvalidSpeedtestMiniServer(SpeedtestException):
    """Server provided as a speedtest mini server does not actually appear
    to be a speedtest mini server
    """
    pass


class ShareResultsConnectFailure(SpeedtestException):
    """Could not connect to speedtest.net API to POST results"""
    pass


class ShareResultsSubmitFailure(SpeedtestException):
    """Unable to successfully POST results to speedtest.net API after
    connection
    """
    pass


class SpeedtestResults(object):
    """Class for holding the results of a speedtest, including:

    Download speed
    Upload speed
    Ping/Latency to test server
    Data about server that the test was run against

    Additionally this class can return a result data as a dictionary or CSV,
    as well as submit a POST of the result data to the speedtest.net API
    to get a share results image link.
    """

    def __init__(self, download=0, upload=0, ping=0, server=dict()):
        self._download = download
        self._upload = upload
        self._ping = ping
        self._server = server
        self._share = None

    @property
    def download(self):
        """Get upload speed result"""
        return self._download

    @download.setter
    def download(self, value):
        """Setter for download speed value"""
        self._download = value

    @property
    def upload(self):
        """Get upload speed result"""
        return self._upload

    @upload.setter
    def upload(self, value):
        """Setter for upload speed value"""
        self._upload = value

    @property
    def ping(self):
        """Get ping/latency value"""
        return self._ping

    @ping.setter
    def ping(self, value):
        """Setter for ping/latency value"""
        self._ping = value

    @property
    def server(self):
        """Get data for server used in the test"""
        return self._server

    @server.setter
    def server(self, value):
        """Setter for server data"""
        self._server = value

    def dict(self):
        """Return dictionary of result data"""

        return dict(download=self.download,
                    upload=self.upload,
                    ping=self.ping,
                    server=self.server['id'])

    def csv(self):
        """Return data in CSV format in the order of:
        Speedtest.net Server ID, Latency/Ping, Download Speed, Upload Speed

        """

        return '%(server)s,%(ping)s,%(download)s,%(upload)s' % self.dict()

    def share(self):
        """POST data to the speedtest.net API to obtain a share results
        link
        """

        if self._share:
            return self._share

        download = int(round((self.download / 1000) * 8, 0))
        ping = int(round(self.ping, 0))
        upload = int(round((self.upload / 1000) * 8, 0))

        # Build the request to send results back to speedtest.net
        # We use a list instead of a dict because the API expects parameters
        # in a certain order
        api_data = [
            'download=%s' % download,
            'ping=%s' % ping,
            'upload=%s' % upload,
            'promo=',
            'startmode=%s' % 'pingselect',
            'recommendedserverid=%s' % self.server['id'],
            'accuracy=%s' % 1,
            'serverid=%s' % self.server['id'],
            'hash=%s' % md5(('%s-%s-%s-%s' %
                             (ping, upload, download, '297aae72'))
                            .encode()).hexdigest()]

        req = Request('http://www.speedtest.net/api/api.php',
                      data='&'.join(api_data).encode())
        req.add_header('Referer', 'http://c.speedtest.net/flash/speedtest.swf')
        try:
            f = urlopen(req)
        except (URLError, HTTPError):
            raise ShareResultsConnectFailure

        response = f.read()
        code = f.code
        f.close()

        if int(code) != 200:
            raise ShareResultsSubmitFailure('Could not submit results to '
                                            'speedtest.net')

        qsargs = parse_qs(response.decode())
        resultid = qsargs.get('resultid')
        if not resultid or len(resultid) != 1:
            raise ShareResultsSubmitFailure('Could not submit results to '
                                            'speedtest.net')

        self._share = 'http://www.speedtest.net/result/%s.png' % resultid[0]

        return self._share


class Speedtest(object):
    """Class for performing standard speedtest.net testing operations"""

    def __init__(self, config=dict()):
        self.config = dict()
        self.get_config()
        self.config.update(config)

        self.servers = dict()
        self.closest = list()
        self.best = dict()

        self._results = SpeedtestResults()

    def results(self):
        """Return a SpeedtestResults object"""
        return self._results

    def get_config(self):
        """Download the speedtest.net configuration and return only the data
        we are interested in
        """

        try:
            uh = urlopen('http://www.speedtest.net/speedtest-config.php')
        except (URLError, HTTPError):
            raise ConfigRetrievalError

        configxml = []
        while 1:
            configxml.append(uh.read(10240))
            if len(configxml[-1]) == 0:
                break
        if int(uh.code) != 200:
            return None

        uh.close()

        try:
            root = ET.fromstring(''.encode().join(configxml))
            server_config = root.find('server-config').attrib
            download = root.find('download').attrib
            upload = root.find('upload').attrib
            times = root.find('times').attrib
            client = root.find('client').attrib

        except AttributeError:
            root = DOM.parseString(''.join(configxml))
            server_config = get_attributes_by_tag_name(root, 'server-config')
            download = get_attributes_by_tag_name(root, 'download')
            upload = get_attributes_by_tag_name(root, 'upload')
            times = get_attributes_by_tag_name(root, 'times')
            client = get_attributes_by_tag_name(root, 'client')

        ignore_servers = map(int, server_config['ignoreids'].split(','))

        sizes = dict(upload=[], download=[])
        for desc, size in times.items():
            if desc.startswith('ul'):
                sizes['upload'].append(int(size))
            elif desc.startswith('dl'):
                sizes['download'].append(int(size) / 10000)

        sizes['upload'].sort()
        sizes['download'].sort()

        counts = dict(upload=int(upload['threadsperurl']),
                      download=int(download['threadsperurl']))

        threads = dict(upload=int(upload['threads']),
                       download=int(server_config['threadcount']))

        self.config.update({
            'client': client,
            'ignore_servers': ignore_servers,
            'sizes': sizes,
            'counts': counts,
            'threads': threads
        })

        self.lat_lon = (float(client['lat']), float(client['lon']))

        del root
        del configxml
        return self.config

    def get_servers(self, servers=[]):
        """Retrieve a the list of speedtest.net servers, optionally filtered
        to servers matching those specified in the ``servers`` argument
        """

        for i, s in enumerate(servers):
            try:
                servers[i] = int(s)
            except ValueError:
                raise InvalidServerIDType('%s is an invalid server type, must '
                                          'be int' % s)

        try:
            uh = urlopen('http://www.speedtest.net/speedtest-servers.php')
        except (URLError, HTTPError):
            raise ServersRetrievalError

        serversxml = []
        while 1:
            serversxml.append(uh.read(10240))
            if len(serversxml[-1]) == 0:
                break
        if int(uh.code) != 200:
            return None

        uh.close()

        try:
            root = ET.fromstring(''.encode().join(serversxml))
            elements = root.getiterator('server')
        except AttributeError:
            root = DOM.parseString(''.join(serversxml))
            elements = root.getElementsByTagName('server')

        for server in elements:
            try:
                attrib = server.attrib
            except AttributeError:
                attrib = dict(list(server.attributes.items()))

            if servers and int(attrib.get('id')) not in servers:
                continue

            if int(attrib.get('id')) in self.config['ignore_servers']:
                continue

            d = distance(self.lat_lon,
                         (float(attrib.get('lat')), float(attrib.get('lon'))))
            attrib['d'] = d

            try:
                self.servers[d].append(attrib)
            except KeyError:
                self.servers[d] = [attrib]

        if servers and not self.servers:
            raise NoMatchedServers

        del root
        del serversxml
        del elements

        return self.servers

    def set_mini_server(self, server):
        """Instead of querying for a list of servers, set a link to a
        speedtest mini server
        """

        name, ext = os.path.splitext(server)
        if ext:
            url = os.path.dirname(server)
        else:
            url = server

        urlparts = urlparse(url)

        try:
            f = urlopen(server)
        except (URLError, HTTPError):
            raise SpeedtestMiniConnectFailure('Failed to connect to %s' %
                                              server)
        else:
            text = f.read()
            f.close()

        extension = re.findall('upload_extension: "([^"]+)"', text.decode())
        if not urlparts or not extension:
            raise InvalidSpeedtestMiniServer('Invalid Speedtest Mini Server: '
                                             '%s' % server)

        self.servers = [{
            'sponsor': 'Speedtest Mini',
            'name': urlparts[1],
            'd': 0,
            'url': '%s/speedtest/upload.%s' % (url.rstrip('/'), extension[0]),
            'latency': 0,
            'id': 0
        }]

        return self.servers

    def get_closest(self, limit=5):
        """Limit servers to the closest speedtest.net servers based on
        geographic distance
        """

        for d in sorted(self.servers.keys()):
            for s in self.servers[d]:
                self.closest.append(s)
                if len(self.closest) == limit:
                    break
            else:
                continue
            break

        return self.closest

    def get_best_server(self, servers):
        """Perform a speedtest.net "ping" to determine which speedtest.net
        server has the lowest latency
        """

        results = {}
        for server in servers:
            cum = []
            url = os.path.dirname(server['url'])
            for _ in range(0, 3):
                try:
                    uh = urlopen('%s/latency.txt' % url)
                except (HTTPError, URLError):
                    cum.append(3600)
                    continue
                start = time.time()
                text = uh.read(9)
                total = time.time() - start
                if int(uh.code) == 200 and text == 'test=test'.encode():
                    cum.append(total)
                else:
                    cum.append(3600)
                uh.close()
            avg = round((sum(cum) / 3) * 1000000, 3)
            results[avg] = server

        fastest = sorted(results.keys())[0]
        best = results[fastest]
        best['latency'] = fastest

        self._results.ping, self._results.server = fastest, best

        self.best.update(best)
        return best

    def download(self, callback=None):
        """Test download speed against speedtest.net"""

        urls = []
        for size in self.config['sizes']['download']:
            for _ in range(0, self.config['counts']['download']):
                urls.append('%s/random%sx%s.jpg' %
                            (os.path.dirname(self.best['url']), size, size))

        url_count = len(urls)

        start = time.time()

        def producer(q, urls, url_count):
            for i, url in enumerate(urls):
                thread = Downloader(url, start)
                thread.start()
                q.put(thread, True)
                if not shutdown_event.isSet() and callback:
                    callback(i, url_count)

        finished = []

        def consumer(q, url_count):
            while len(finished) < url_count:
                thread = q.get(True)
                while thread.isAlive():
                    thread.join(timeout=0.1)
                finished.append(sum(thread.result))
                del thread

        q = Queue(self.config['threads']['download'])
        prod_thread = threading.Thread(target=producer,
                                       args=(q, urls, url_count))
        cons_thread = threading.Thread(target=consumer, args=(q, url_count))
        start = time.time()
        prod_thread.start()
        cons_thread.start()
        while prod_thread.isAlive():
            prod_thread.join(timeout=0.1)
        while cons_thread.isAlive():
            cons_thread.join(timeout=0.1)

        self._results.download = (sum(finished) / (time.time() - start))
        return self._results.download

    def upload(self, callback=None):
        """Test upload speed against speedtest.net"""

        sizes = []

        for size in self.config['sizes']['upload']:
            for _ in range(0, self.config['counts']['upload']):
                sizes.append(size)

        size_count = len(sizes)

        start = time.time()

        def producer(q, sizes, size_count):
            for i, size in enumerate(sizes):
                thread = Uploader(self.best['url'], start, size)
                thread.start()
                q.put(thread, True)
                if not shutdown_event.isSet() and callback:
                    callback(i, size_count)

        finished = []

        def consumer(q, size_count):
            while len(finished) < size_count:
                thread = q.get(True)
                while thread.isAlive():
                    thread.join(timeout=0.1)
                finished.append(thread.result)
                del thread

        q = Queue(6)
        prod_thread = threading.Thread(target=producer,
                                       args=(q, sizes, size_count))
        cons_thread = threading.Thread(target=consumer, args=(q, size_count))
        start = time.time()
        prod_thread.start()
        cons_thread.start()
        while prod_thread.isAlive():
            prod_thread.join(timeout=0.1)
        while cons_thread.isAlive():
            cons_thread.join(timeout=0.1)

        self._results.upload = (sum(finished) / (time.time() - start))
        return self._results.upload


def ctrl_c(signum, frame):
    """Catch Ctrl-C key sequence and set a shutdown_event for our threaded
    operations
    """

    global shutdown_event
    shutdown_event.set()
    raise SystemExit('\nCancelling...')


def version():
    """Print the version"""

    raise SystemExit(__version__)


def parse_args():
    description = (
        'Command line interface for testing internet bandwidth using '
        'speedtest.net.\n'
        '------------------------------------------------------------'
        '--------------\n'
        'https://github.com/sivel/speedtest-cli')

    parser = ArgParser(description=description)
    # Give optparse.OptionParser an `add_argument` method for
    # compatibility with argparse.ArgumentParser
    try:
        parser.add_argument = parser.add_option
    except AttributeError:
        pass
    parser.add_argument('--bytes', dest='units', action='store_const',
                        const=('bytes', 1), default=('bits', 8),
                        help='Display values in bytes instead of bits. Does '
                             'not affect the image generated by --share')
    parser.add_argument('--share', action='store_true',
                        help='Generate and provide a URL to the speedtest.net '
                             'share results image')
    parser.add_argument('--simple', action='store_true',
                        help='Suppress verbose output, only show basic '
                             'information')
    parser.add_argument('--list', action='store_true',
                        help='Display a list of speedtest.net servers '
                             'sorted by distance')
    parser.add_argument('--server', help='Specify a server ID to test against',
                        type=PARSER_TYPE_INT)
    parser.add_argument('--mini', help='URL of the Speedtest Mini server')
    parser.add_argument('--source', help='Source IP address to bind to')
    parser.add_argument('--version', action='store_true',
                        help='Show the version number and exit')

    options = parser.parse_args()
    if isinstance(options, tuple):
        args = options[0]
    else:
        args = options
    return args


def shell():
    """Run the full speedtest.net test"""

    global shutdown_event, source
    shutdown_event = threading.Event()

    signal.signal(signal.SIGINT, ctrl_c)

    args = parse_args()

    # Print the version and exit
    if args.version:
        version()

    # If specified bind to a specific IP address
    if args.source:
        source = args.source
        socket.socket = bound_socket

    # Don't set a callback if we are running in simple mode
    if args.simple:
        callback = None
    else:
        callback = print_dots

    print_('Retrieving speedtest.net configuration...')
    try:
        speedtest = Speedtest()
    except ConfigRetrievalError:
        print_('Cannot retrieve speedtest configuration')

    if args.list:
        try:
            speedtest.get_servers()
        except ServersRetrievalError:
            print_('Cannot retrieve speedtest server list')
            sys.exit(1)

        server_list = []
        for _, servers in sorted(speedtest.servers.items()):
            for server in servers:
                line = ('%(id)5s) %(sponsor)s (%(name)s, %(country)s) '
                        '[%(d)0.2f km]' % server)
                server_list.append(line)
        # Python 2.7 and newer seem to be ok with the resultant encoding
        # from parsing the XML, but older versions have some issues.
        # This block should detect whether we need to encode or not
        try:
            unicode()
            print_('\n'.join(server_list).encode('utf-8', 'ignore'))
        except NameError:
            print_('\n'.join(server_list))
        except IOError:
            pass
        sys.exit(0)

    # Set a filter of servers to retrieve
    servers = []
    if args.server:
        servers.append(args.server)

    if not args.simple:
        print_('Testing from %(isp)s (%(ip)s)...' % speedtest.config['client'])
    if not args.mini:
        print_('Retrieving speedtest.net server list...')
        try:
            speedtest.get_servers(servers)
        except NoMatchedServers:
            print_('No matched servers: %s' % args.server)
            sys.exit(1)
        except ServersRetrievalError:
            print_('Cannot retrieve speedtest server list')
            sys.exit(1)
        except InvalidServerIDType:
            print_('%s is an invalid server type, must be int' % args.server)
            sys.exit(1)

        if not args.simple:
            print_('Selecting best server based on ping...')
        speedtest.get_best_server(speedtest.get_closest())
    elif args.mini:
        speedtest.get_best_server(speedtest.set_mini_server(args.mini))

    results = speedtest.results()

    if args.simple:
        print_('Ping: %(latency)s ms' % results.server)
    else:
        # Python 2.7 and newer seem to be ok with the resultant encoding
        # from parsing the XML, but older versions have some issues.
        # This block should detect whether we need to encode or not
        try:
            unicode()
            print_(('Hosted by %(sponsor)s (%(name)s) [%(d)0.2f km]: '
                    '%(latency)s ms' %
                    results.server).encode('utf-8', 'ignore'))
        except NameError:
            print_('Hosted by %(sponsor)s (%(name)s) [%(d)0.2f km]: '
                   '%(latency)s ms' % results.server)

    if not args.simple:
        print_('Testing upload speed', end='')
    speedtest.download(callback=callback)
    print_('Download: %0.2f M%s/s' %
           ((results.download / 1000 / 1000) * args.units[1], args.units[0]))

    if not args.simple:
        print_('Testing download speed', end='')
    speedtest.upload(callback=callback)
    print_('Upload: %0.2f M%s/s' %
           ((results.upload / 1000 / 1000) * args.units[1], args.units[0]))


def main():
    try:
        shell()
    except KeyboardInterrupt:
        print_('\nCancelling...')


if __name__ == '__main__':
    main()
