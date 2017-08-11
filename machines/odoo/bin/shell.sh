#!/bin/bash
[[ "$VERBOSE" == "1" ]] && set -x

rsync /opt/src/admin/ /opt/openerp/admin -arP --delete --exclude=.git
rsync /opt/src/customs/$CUSTOMS/ /opt/openerp/active_customs -arP --exclude=.git
/opt/openerp/admin/oeln $CUSTOMS
chown odoo:odoo /opt/openerp/active_customs -R
sudo -E -H -u odoo /opt/openerp/versions/server/odoo.py shell -d $DBNAME -c /home/odoo/config_debug
