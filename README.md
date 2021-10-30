# Odoo Docker Framework

Provides functionalities:

- setup empty new odoo with one bash command `odoo init --version 15.0`

- setup full fleged odoo server environment containing
  - supports MANIFEST file in odoo root directory to install and uninstall modules
  - postgres with pg_activity, enhanced pgcli
  - fake webmail to receive AND SEND mails
  - logs.io integration to display container output on web browser
  - dividing web, cron, queuejob container per default
  - progress bar on postgres dump/restore

- fzf compatible creation of and AST of your project

- fast restore / create snapshots of complete databases using btrfs, which helps testing things on customer databases

## How to install

- clone repository to /opt/odoo
- make a bin file /usr/local/bin/odoo and chmod a+x
```
#!/bin/bash
sudo -E /opt/odoo/odoo "$@"
```
- make entry `/etc/sudoers.d/odoo
```
Cmnd_Alias ODOO_COMMANDS_ODOO = /usr/bin/find *, /opt/odoo/odoo *, /usr/bin/btrfs subvolume *, /usr/bin/mkdir *, /usr/bin/mv *, /usr/bin/rsync *, /usr/bin/rm *,  /usr/bin/du *, /usr/local/bin/odoo *, /opt/odoo/odoo *, /usr/bin/btrfs subvol show *, /usr/sbin/gosu *
odoo ALL=NOPASSWD:SETENV: ODOO_COMMANDS_ODOO
```

## Make new empty odoo

- make an empty directory and cd into it
- then:

```
odoo init
```

## Store settings not in ~/.odoo but local inside the current directory in .odoo 

This is excellent for jenkins jobs where different branches are tested.

```
odoo reload --local --devmode --headless --project-name 'unique_name'
```

## How to extend an existing service
------------------------------------

- make a docker-compose file like ~/.odoo/docker-compose.yml

```
services:
  odoo3:
    labels:
      compose.merge: base-machine
    environment:
      WHAT YOU WANT
    volumes:
      WHAT YOU WANT

```

### Example for fixed ip addresses:
```
services:
    proxy:
        networks:
            network1:
                ipv4_address: 10.5.0.6
networks:
    network1:
        driver: bridge
        ipam:
            config:
                - subnet: 10.5.0.0/16
```