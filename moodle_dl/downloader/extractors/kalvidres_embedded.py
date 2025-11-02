# coding: utf-8
from __future__ import unicode_literals

import re
import urllib.parse

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import ExtractorError


class KalvidresEmbeddedIE(InfoExtractor):
    """
    Extractor for embedded Kaltura videos that use direct lti_launch.php URLs
    (e.g., videos extracted from Book chapter HTML)

    These URLs contain the Kaltura source URL directly in the 'source' parameter,
    so we can extract it without making HTTP requests.
    """
    IE_NAME = 'kalvidresEmbedded'
    _VALID_URL = r'(?P<scheme>https?://)(?P<host>[^/]+)(?P<path>.*)?/filter/kaltura/lti_launch\.php\?.*'

    def _real_extract(self, url):
        # Parse the URL to extract the 'source' parameter
        # The source parameter contains the actual Kaltura browse/embed URL
        parsed_url = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed_url.query)

        # Get the source parameter (it's URL-encoded)
        source_list = params.get('source', [])
        if not source_list:
            raise ExtractorError('Unable to extract source parameter from lti_launch URL')

        kaltura_source = source_list[0]  # source is the Kaltura browse/embed URL

        # Extract entry ID from the Kaltura URL
        # Example: https://kaf.keats.kcl.ac.uk/browseandembed/index/media/entryid/1_er5gtb0g/...
        entry_id_match = re.search(r'/entryid/([^/]+)', kaltura_source)
        if not entry_id_match:
            raise ExtractorError(f'Unable to extract entry ID from Kaltura source URL: {kaltura_source}')

        entry_id = entry_id_match.group(1)

        # Fetch the Kaltura browse/embed page to extract partner ID
        # Modern Kaltura MediaSpace uses React and stores config in JSON
        browse_page = self._download_webpage(kaltura_source, entry_id, 'Downloading Kaltura browse/embed page')

        # Extract partner ID from the page
        # The page contains JSON data with partnerId field
        partner_id_match = re.search(r'"partnerId"\s*:\s*(\d+)', browse_page)
        if not partner_id_match:
            raise ExtractorError(f'Unable to extract partner ID from Kaltura browse/embed page')

        partner_id = partner_id_match.group(1)

        # Construct the Kaltura URL in the format that KalturaIE recognizes
        # Format: kaltura:partner_id:entry_id
        kaltura_url = f'kaltura:{partner_id}:{entry_id}'

        return {
            '_type': 'url',
            'url': kaltura_url,
            'ie_key': 'Kaltura',
        }
