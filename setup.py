from distutils.core import setup
setup(
   name='pwbatch',
   version='1.0',
   packages=['pwbatch'],
   license='MIT',
   entry_points={
       'console_scripts': [
           'pwbatch = pwbatch:main',
       ]
   },
   install_requires=['pwclient'],
)
