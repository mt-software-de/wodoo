#!/bin/bash

echo "Installing custom requirements from odoo"

. /usr/local/bin/virtualenvwrapper.sh

#7.0
if [[ "$1" == "7.0" ]]; then
    echo "Installing version 7.0 requirements"
    mkvirtualenv 7.0
    pip install -r /root/requirements_70.txt
    pip install -r /root/requirements.txt
else
    echo "Installing version $1 requirements"
    mkvirtualenv $1
    wget https://raw.githubusercontent.com/odoo/odoo/$1/requirements.txt -O /root/requirements_$1.txt
    pip install -r /root/requirements_$1.txt
    pip install -r /root/requirements.txt
fi

