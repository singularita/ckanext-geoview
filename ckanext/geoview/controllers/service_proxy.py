from logging import getLogger
from ckan import plugins as p
import urlparse

import requests

import ckan.logic as logic
import ckan.lib.base as base
from urllib import urlencode

log = getLogger(__name__)

MAX_FILE_SIZE = 3 * 1024 * 1024  # 1MB
CHUNK_SIZE = 512

# HTTP request parameters that may conflict with OGC services protocols and should be excluded from proxied calls
OGC_EXCLUDED_PARAMS = ['service', 'version', 'request', 'outputformat', 'typename', 'layers', 'srsname', 'bbox', 'maxfeatures']

def proxy_service_resource(self, context, data_dict):
    ''' Chunked proxy for resources. To make sure that the file is not too
    large, first, we try to get the content length from the headers.
    If the headers to not contain a content length (if it is a chinked
    response), we only transfer as long as the transferred data is less
    than the maximum file size. '''
    resource_id = data_dict['resource_id']
    log.info('Proxify resource {id}'.format(id=resource_id))
    resource = logic.get_action('resource_show')(context, {'id': resource_id})
    url = resource['url']

    return proxy_service_url(self, url)

def proxy_service_url(self, url):

    parts = urlparse.urlsplit(url)
    if not parts.scheme or not parts.netloc:
        base.abort(409, detail='Invalid URL.')

    try:
        req = self._py_object.request
        method = req.environ["REQUEST_METHOD"]

        params = urlparse.parse_qs(parts.query)

        if (not p.toolkit.asbool(base.config.get('ckanext.geoview.forward_ogc_request_params', 'False'))):
            # remove query parameters that may conflict with OGC protocols
            for key in dict(params):
                if key.lower() in OGC_EXCLUDED_PARAMS:
                    del params[key]
            parts = parts._replace(query = urlencode(params))

        parts = parts._replace(fragment = '') # remove potential fragment
        url = parts.geturl()
        if method == "POST":
            length = int(req.environ["CONTENT_LENGTH"])
            headers = {"Content-Type": req.environ["CONTENT_TYPE"]}
            body = req.body
            r = requests.post(url, data=body, headers=headers, stream=True)
        else:
            r = requests.get(url, params=req.query_string, stream=True)

        #log.info('Request: {req}'.format(req=r.request.url))
        #log.info('Request Headers: {h}'.format(h=r.request.headers))

        cl = r.headers.get('content-length')
        if cl and int(cl) > MAX_FILE_SIZE:
            base.abort(409, ('''Content is too large to be proxied. Allowed
                file size: {allowed}, Content-Length: {actual}. Url: '''+url).format(
                allowed=MAX_FILE_SIZE, actual=cl))

        base.response.content_type = r.headers['content-type']
        base.response.charset = r.encoding

        length = 0
        for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
            base.response.body_file.write(chunk)
            length += len(chunk)

            if length >= MAX_FILE_SIZE:
                base.abort(409, ('''Content is too large to be proxied. Allowed
                file size: {allowed}, Content-Length: {actual}. Url: '''+url).format(
                    allowed=MAX_FILE_SIZE, actual=length))

    except requests.exceptions.HTTPError, error:
        details = 'Could not proxy resource. Server responded with %s %s' % (
            error.response.status_code, error.response.reason)
        base.abort(409, detail=details)
    except requests.exceptions.ConnectionError, error:
        details = '''Could not proxy resource because a
                            connection error occurred. %s''' % error
        base.abort(502, detail=details)
    except requests.exceptions.Timeout, error:
        details = 'Could not proxy resource because the connection timed out.'
        base.abort(504, detail=details)


class ServiceProxyController(base.BaseController):
    def proxy_service(self, resource_id):
        data_dict = {'resource_id': resource_id}
        context = {'model': base.model, 'session': base.model.Session,
                   'user': base.c.user or base.c.author}
        return proxy_service_resource(self, context, data_dict)

    def proxy_service_url(self, map_id = None):
        req = self._py_object.request
        if ('ckanext.spatial.common_map.'+map_id+'.url') in base.config:
            # backward compatible with old geoview config
            url = base.config.get('ckanext.spatial.common_map.'+map_id+'.url')
        elif ('ckanext.geoview.basemaps_map') in base.config:
            # check if exists in basemaps config
            url = base.config['ckanext.geoview.basemaps_map'].get(map_id)['url']

        return proxy_service_url(self, url)
