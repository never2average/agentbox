from pathlib import Path

from setuptools import setup, find_packages

setup(
    name='agentbox',
    version='2.0.0',
    description='Kubernetes primitives for running a company-wide AI stack',
    long_description=(Path(__file__).parent / 'README.md').read_text(),
    long_description_content_type='text/markdown',
    license='MIT',
    url='https://github.com/never2average/agentbox',
    py_modules=['ai_ctl'],
    install_requires=[
        'click>=8.1.0',
        'kubernetes>=28.0.0',
        'PyYAML>=6.0.0',
        'jsonschema>=4.17.0',
    ],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: System Administrators',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Topic :: System :: Systems Administration',
    ],
    entry_points={
        'console_scripts': [
            'ai-ctl=ai_ctl:cli',
        ],
    },
    python_requires='>=3.8',
)

