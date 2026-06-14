# -*- coding: utf-8 -*-
from os import path

from setuptools import find_packages, setup

# Get the version from moodle_dl/version.py without importing the package
exec(compile(open('moodle_dl/version.py').read(), 'moodle_dl/version.py', 'exec'))


def readme():
    this_directory = path.abspath(path.dirname(__file__))
    with open(path.join(this_directory, 'README.md'), encoding='utf-8') as f:
        return f.read()


setup(
    name='moodle-dl',
    version=__version__,
    description='Moodle-DL downloads course content fast from Moodle (eg. lecture pdfs)',
    long_description=readme(),
    long_description_content_type='text/markdown',
    url='https://github.com/C0D3D3V/Moodle-DL',
    project_urls={
        'Documentation': 'https://github.com/C0D3D3V/Moodle-DL#readme',
        'Wiki': 'https://github.com/C0D3D3V/Moodle-DL/wiki',
        'Source': 'https://github.com/C0D3D3V/Moodle-DL',
        'Tracker': 'https://github.com/C0D3D3V/Moodle-DL/issues',
    },
    author='C0D3D3V',
    author_email='moodle-dl@ist-ein-knaller.de',
    license='GPL-3.0',
    packages=find_packages(),
    include_package_data=True,
    entry_points={
        'console_scripts': [
            'moodle-dl = moodle_dl.main:main',
        ],
    },
    python_requires='>=3.9',
    install_requires=[
        # HTTP / async HTTP
        'aiodns>=3.0.0',
        'aiofiles>=22.1.0',
        'aiohttp>=3.8.4',
        # TLS / certificates
        'certifi>=2020.4.5.2',
        # Terminal coloring / structured logging
        'colorama>=0.4.6',
        'colorlog>=6.7.0',
        # HTML→text conversion
        'html2text>=2020.1.16',
        # Browser automation (used for SSO login)
        'playwright>=1.40.0',
        # PHP-serialized payloads (Moodle web service response format)
        'phpserialize>=1.3',
        # Keypress reading (used in interactive prompts)
        'readchar>=4.0.3',
        # Sync HTTP
        'requests>=2.30.0',
        'urllib3>=1.26.0,<3',  # requests 2.30+ requires urllib3<3
        # Error reporting
        'sentry_sdk>=0.13.5',
        # XMPP notifications
        'xmpppy>=0.7.1',
        # Media extraction (Kaltura / Helixmedia / LTI)
        'yt_dlp>=2024.3.10',
    ],
    extras_require={
        'test': [
            'pytest>=8.0',
            'pytest-asyncio>=0.21',
            'pytest-randomly>=4.0',
            'hypothesis>=6.0',
        ],
    },
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Environment :: Console',
        'Intended Audience :: End Users/Desktop',
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
        'Programming Language :: Python :: 3 :: Only',
        'Topic :: Education',
        'Topic :: Internet :: WWW/HTTP :: Indexing/Search',
        'Topic :: Multimedia :: Video',
        'Topic :: Multimedia :: Sound/Audio',
        'Topic :: Utilities',
    ],
    zip_safe=False,
)
