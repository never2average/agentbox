from setuptools import setup, find_packages

setup(
    name='ai-ctl',
    version='1.0.0',
    description='AI-powered Kubernetes cluster management CLI',
    author='Your Name',
    py_modules=['ai_ctl'],
    install_requires=[
        'click>=8.1.0',
        'kubernetes>=28.0.0',
        'PyYAML>=6.0.0',
    ],
    entry_points={
        'console_scripts': [
            'ai-ctl=ai_ctl:cli',
        ],
    },
    python_requires='>=3.8',
)

