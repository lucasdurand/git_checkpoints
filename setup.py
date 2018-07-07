from setuptools import setup

setup(
    name='gitcheckpoints',
    version='0.1',
    description='A simple implementation of Jupyter Notebook Checkpoints using Git',
    author='Lucas Durand',
    author_email='lucas@lucasdurand.xyz',
    packages=['gitcheckpoints'],
    install_requires=['brigit'],
    zip_safe=True
)