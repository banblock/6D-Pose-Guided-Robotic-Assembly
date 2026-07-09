from setuptools import find_packages, setup
import os
from glob import glob
package_name = 'foundationpose_client'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ("share/" + package_name + "/resource", glob("resource/*")),
    ],
    install_requires=['setuptools', "requests", "numpy"],
    zip_safe=True,
    maintainer='hungeunlee',
    maintainer_email='dlgnsrms00@naver.com',
    description='TODO: Package description',
    license='TODO: License declaration',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            "foundationpose_client_node = foundationpose_client.foundationpose_node:main"
        ],
    },
)
