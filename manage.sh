#!/bin/bash
# Basic Rules:
# - if a command would stop production run, then ask to continue is done before
# - if in set -e environment and piping commands like cat ... |psql .. then use: pipe=$(mktemp -u); mkfifo $pipe; do.. > $pipe &; do < $pipe
#
# Important Githubs:
#   * https://github.com/docker/compose/issues/2293  -> /usr/local/bin/docker-compose needed
#   * there is a bug: https://github.com/docker/compose/issues/3352  --> using -T
#

function startup() {
	set -e
	[[ "$VERBOSE" == "1" ]] && set -x

	args=("$@")
	DIR=$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )
	ALL_PARAMS=${@:2} # all parameters without command
	export odoo_manager_started_once=1
}

function default_confs() {
	export ODOO_FILES=$DIR/data/odoo.files
	export ODOO_UPDATE_START_NOTIFICATION_TOUCH_FILE=$DIR/run/update_started
	export RUN_POSTGRES=1
	export DB_PORT=5432
}

function export_settings() {
    # set variables from settings
    while read line; do
        # reads KEY1=A GmbH and makes export KEY1="A GmbH" basically
        [[ "$line" == '#*' ]] && continue
        [[ "$line" == '' ]] && continue
        var="${line%=*}"
        value="${line##*=}"
        eval "$var=\"$value\""
    done <$DIR/settings
    export $(cut -d= -f1 $DIR/settings)  # export vars now in local variables

	if [[ "$RUN_POSTGRES" == "1" ]]; then
		DB_HOST=postgres
		DB_PORT=5432
		DB_USER=odoo
		DB_PWD=odoo
	fi

	# get odoo version
	export ODOO_VERSION=$(
	cd $ODOO_HOME/data/src/admin/module_tools
	python <<-EOF
	import odoo_config
	v = odoo_config.get_version_from_customs("$CUSTOMS")
	print v
	EOF
	)
}

function restore_check() {
	dumpname=$(basename $2)
	if [[ ! "${dumpname%.*}" == *"$DBNAME"* ]]; then
		echo "The dump-name \"$dumpname\" should somehow match the current database \"$DBNAME\", which isn't."
		exit -1
	fi

}

function remove_postgres_connections() {
	echo "Removing all current connections"
	SQL=$(cat <<-EOF
		SELECT pg_terminate_backend(pg_stat_activity.pid)
		FROM pg_stat_activity 
		WHERE pg_stat_activity.datname = '$DBNAME' 
		AND pid <> pg_backend_pid(); 
		EOF
		)
	echo "$SQL" | $0 psql
}

function do_restore_db_in_docker_container () {
	# remove the postgres volume and reinit

	echo "Restoring dump within docker container postgres"
	dump_file=$1
	$dc kill
	$dc rm -f || true
	if [[ "$RUN_POSTGRES" == 1 ]]; then
		askcontinue "Removing docker volume postgres-data (irreversible)"
	fi
	VOLUMENAME=${PROJECT_NAME}_postgresdata
	docker volume ls |grep -q $VOLUMENAME && docker volume rm $VOLUMENAME 
	LOCAL_DEST_NAME=$DIR/run/restore/$DBNAME.gz
	[[ -f "$LOCAL_DEST_NAME" ]] && rm $LOCAL_DEST_NAME

	/bin/ln $dump_file $LOCAL_DEST_NAME
	$0 reset-db
	$dcrun postgres /restore.sh $(basename $LOCAL_DEST_NAME)
}

function do_restore_db_on_external_postgres () {
	echo "Restoring dump on $DB_HOST"
	dump_file=$1
	echo "Using Host: $DB_HOST, Port: $DB_PORT, User: $DB_USER, ...."
	export PGPASSWORD=$DB_PWD
	ARGS="-h $DB_HOST -p $DB_PORT -U $DB_USER"
	PSQL="psql $ARGS"
	DROPDB="dropdb $ARGS"
	CREATEDB="createdb $ARGS"
	PGRESTORE="pg_restore $ARGS"

	remove_postgres_connections
	eval "$DROPDB $DBNAME" || echo "Failed to drop $DBNAME"
	eval "$CREATEDB $DBNAME"
	pipe=$(mktemp -u)
	mkfifo "$pipe"
	gunzip -c $1 > $pipe &
	echo "Restoring Database $DBNAME"
	$PGRESTORE -d $DBNAME < $pipe
}

function do_restore_files () {
	# remove the postgres volume and reinit
	tararchive_full_path=$1
	LOCAL_DEST_NAME=$DIR/run/restore/odoofiles.tar
	[[ -f "$LOCAL_DEST_NAME" ]] && rm $LOCAL_DEST_NAME

	/bin/ln $tararchive_full_path $LOCAL_DEST_NAME
	$dcrun odoo /bin/restore_files.sh $(basename $LOCAL_DEST_NAME)
}

function askcontinue() {
	echo $1
	force=0
	echo "$*" |grep -q '[-]force' && {
		force=1
	}
	if [[ "$force" == "0" && "$ASK_CONTINUE" == "0" ]]; then
		if [[ -z "$1" ]]; then
			echo "Ask continue disabled, continueing..."
		fi
	else
		read -p "Continue? (Ctrl+C to break)" || {
			exit -1
		}
	fi
}

function showhelp() {
    echo Management of odoo instance
    echo
    echo
	echo ./manage.sh sanity-check
    echo Reinit fresh db:
    echo './manage.sh reset-db'
    echo
    echo Update:
    echo './manage.sh update [module]'
    echo 'Just custom modules are updated, never the base modules (e.g. prohibits adding old stock-locations)'
    echo 'Minimal downtime - but there is a downtime, even for phones'
    echo 
    echo "Please call manage.sh springclean|update|backup|run_standalone|upall|attach_running|rebuild|restart"
    echo "attach <machine> - attaches to running machine"
	echo ""
    echo "backup <backup-dir> - backup database and/or files to the given location with timestamp; if not directory given, backup to dumps is done "
	echo ""
    echo "backup-db <backup-dir>"
	echo ""
    echo "backup-files <backup-dir>"
	echo ""
    echo "debug <machine-name> - starts /bin/bash for just that machine and connects to it; if machine is down, it is powered up; if it is up, it is restarted; as command an endless bash loop is set"
	echo ""
    echo "build - no parameter all machines, first parameter machine name and passes other params; e.g. ./manage.sh build asterisk --no-cache"
	echo ""
    echo "install-telegram-bot - installs required python libs; execute as sudo"
	echo ""
    echo "telegram-setup- helps creating a permanent chatid"
	echo ""
    echo "kill - kills running machines"
	echo ""
    echo "logs - show log output; use parameter to specify machine"
	echo ""
    echo "logall - shows log til now; use parameter to specify machine"
	echo ""
    echo "make-CA - recreates CA caution!"
	echo ""
    echo "make-keys - creates VPN Keys for CA, Server, Asterisk and Client. If key exists, it is not overwritten"
	echo ""
    echo "springclean - remove dead containers, untagged images, delete unwanted volums"
	echo ""
    echo "rm - command"
	echo ""
    echo "rebuild - rebuilds docker-machines - data not deleted"
	echo ""
    echo "restart - restarts docker-machine(s) - parameter name"
	echo ""
    echo "restore <filepathdb> <filepath_tarfiles> [-force] - restores the given dump as odoo database"
	echo ""
    echo "restore-dev-db - Restores database dump regularly and then applies scripts to modify it, so it can be used for development (adapting mailserver, disable cronjobs)"
	echo ""
    echo "runbash <machine name> - starts bash in NOT RUNNING container (a separate one)"
	echo ""
    echo "setup-startup makes skript in /etc/init/${CUSTOMS}"
	echo ""
    echo "stop - like docker-compose stop"
	echo ""
    echo "quickpull - fetch latest source, oeln - good for mako templates"
	echo ""
	echo "turn-into-dev - applies scripts to make the database a dev database"
	echo ""
    echo "update <machine name>- fetch latest source code of modules and run update of just custom modules; machines are restarted after that"
	echo ""
    echo "update-source - sets the latest source code in the containers"
	echo ""
    echo "up - starts all machines equivalent to service <service> start "
    echo
}

if [ -z "$1" ]; then
    showhelp
    exit -1
fi

function prepare_filesystem() {
    mkdir -p $DIR/run/config
}

function replace_all_envs_in_file() {
	if [[ ! -f "$1" ]]; then
		echo "File not found: $1"
		exit -1
	fi
	export FILENAME=$1
	$(python <<-"EOF"
	import os
	import re
	filepath = os.environ['FILENAME']
	with open(filepath, 'r') as f:
	    content = f.read()
	all_params = re.findall(r'\$\{[^\}]*?\}', content)
	for param in all_params:
	    name = param
	    name = name.replace("${", "")
	    name = name.replace("}", "")
	    content = content.replace(param, os.environ[name])
	with open(filepath, 'w') as f:
	    f.write(content)
	EOF
	)
}

function prepare_yml_files_from_template_files() {
    # replace params in configuration file
    # replace variables in docker-compose;
    cd $DIR

	if [[ "$odoo_manager_started_once" != "1" ]]; then
		echo "CUSTOMS: $CUSTOMS"
		echo "DB: $DBNAME"
		echo "VERSION: $ODOO_VERSION"
		echo "FILES: $ODOO_FILES"
	fi

	# python: find all configuration files from machines folder; extract sort 
	# by manage-sort flag and put file into run directory
	# only if RUN_parentpath like RUN_ODOO is <> 0 include the machine
	#
	# - also replace all environment variables
	find $DIR/run -name *docker-compose*.yml -delete
	ALL_CONFIG_FILES=$(cd $DIR; find machines -name 'docker-compose.yml')
	ALL_CONFIG_FILES=$(python <<-EOF
	import os
	import shutil
	import re
	paths = """$ALL_CONFIG_FILES""".split("\n")
	dest_files = []
	for path in paths:
	    with open(path, 'r') as f:
	        content = f.read()
			# dont matter if written manage-order: or manage-order 
	        order = content.split("manage-order")[1].split("\n")[0].replace(":", "").strip()
	    folder_name = os.path.basename(os.path.dirname(path))
	    if os.getenv("RUN_{}".format(folder_name.upper()), "1") == "0":
	        continue
	    dest_file = 'run/{}-docker-compose.{}.yml'.format(order, folder_name)
	    shutil.copy(path, dest_file)
	    dest_files.append(dest_file)
	for x in sorted(dest_files):
	    print x.replace("run/", "")
	EOF
	)
	cd $DIR
    for file in $ALL_CONFIG_FILES; do
		replace_all_envs_in_file run/$file
    done

	# translate config files for docker compose with appendix -f
    ALL_CONFIG_FILES="$(for f in ${ALL_CONFIG_FILES}; do echo "-f run/$f" | tr '\n' ' '; done)"

	# append custom docker composes
	if [[ -n "$ADDITIONAL_DOCKER_COMPOSE" ]]; then
		cp $ADDITIONAL_DOCKER_COMPOSE $DIR/run
		for file in $ADDITIONAL_DOCKER_COMPOSE; do
			ALL_CONFIG_FILES+=" -f "
			ALL_CONFIG_FILES+=$file
		done
	fi
	echo $ALL_CONFIG_FILES

    dc="/usr/local/bin/docker-compose -p $PROJECT_NAME $ALL_CONFIG_FILES"
    dcrun="$dc run -T"
    dcexec="$dc exec -T"
}


function do_command() {
    case $1 in
    setup-startup)
        PATH=$DIR

        if [[ -f /sbin/initctl ]]; then
            # ubuntu 14.04 upstart
            file=/etc/init/${CUSTOMS}_odoo.conf

            echo "Setting up upstart script in $file"
            /bin/cp $DIR/config/upstart $file
            /bin/sed -i -e "s/\${DCPREFIX}/$DCPREFIX/" -e "s/\${DCPREFIX}/$DCPREFIX/" $file
            /bin/sed -i -e "s|\${PATH}|$PATH|" -e "s|\${PATH}|$PATH|" $file
            /bin/sed -i -e "s|\${CUSTOMS}|$CUSTOMS|" -e "s|\${CUSTOMS}|$CUSTOMS|" $file
            /sbin/initctl reload-configuration
        else
            echo "Setting up systemd script for startup"
            servicename=${CUSTOMS}_odoo.service
            file=/lib/systemd/system/$servicename

            echo "Setting up upstart script in $file"
            /bin/cp $DIR/config/systemd $file
            /bin/sed -i -e "s/\${DCPREFIX}/$DCPREFIX/" -e "s/\${DCPREFIX}/$DCPREFIX/" $file
            /bin/sed -i -e "s|\${PATH}|$PATH|" -e "s|\${PATH}|$PATH|" $file
            /bin/sed -i -e "s|\${CUSTOMS}|$CUSTOMS|" -e "s|\${CUSTOMS}|$CUSTOMS|" $file

            set +e
            /bin/systemctl disable $servicename
            /bin/rm /etc/systemd/system/$servicename
            /bin/rm lib/systemd/system/$servicename
            /bin/systemctl daemon-reload
            /bin/systemctl reset-failed
            /bin/systemctl enable $servicename
            /bin/systemctl start $servicename
        fi
        ;;
    exec)
        $dc exec $2 $3 $3 $4
        ;;
    backup-db)
        if [[ -n "$2" ]]; then
            BACKUPDIR=$2
        else
            BACKUPDIR=$DIR/dumps
        fi
        filename=$DBNAME.$(date "+%Y-%m-%d_%H%M%S").dump.gz
        filepath=$BACKUPDIR/$filename
        LINKPATH=$DIR/dumps/latest_dump
		if [[ "$RUN_POSTGRES" == "1" ]]; then
			$dc up -d postgres odoo
			# by following command the call is crontab safe;
			docker exec -i $($dc ps -q postgres) /backup.sh
			mv $DIR/dumps/$DBNAME.gz $filepath
		else
			pg_dump -Z0 -Fc $DBNAME | pigz --rsyncable > $filepath
		fi
        /bin/rm $LINKPATH || true
        ln -s $filepath $LINKPATH
        md5sum $filepath
        echo "Dumped to $filepath"
        ;;
    backup-files)
        if [[ -n "$2" ]]; then
            BACKUPDIR=$2
        else
            BACKUPDIR=$DIR/dumps
        fi
        BACKUP_FILENAME=$CUSTOMS.files.tar.gz
        BACKUP_FILEPATH=$BACKUPDIR/$BACKUP_FILENAME

		$dcrun odoo /backup_files.sh
        [[ -f $BACKUP_FILEPATH ]] && rm -Rf $BACKUP_FILEPATH
        mv $DIR/dumps/odoofiles.tar $BACKUP_FILEPATH

        echo "Backup files done to $BACKUP_FILEPATH"
        ;;

    backup)
		$0 backup-db $ALL_PARAMS
		$0 backup-files $ALL_PARAMS
        ;;
    reset-db)
		echo "$*" |grep -q '[-]force' || {
            askcontinue "Deletes database $DBNAME!"
		}
		if [[ "$RUN_POSTGRES" != "1" ]]; then
			echo "Postgres container is disabled; cannot reset external database"
			exit -1
		fi
        echo "Stopping all services and creating new database"
        echo "After creation the database container is stopped. You have to start the system up then."
        $dc kill
        $dcrun -e INIT=1 postgres /entrypoint2.sh
        echo
        echo 
        echo
        echo "Database initialized. You have to restart now."

        ;;

	restore-files)
        if [[ -z "$2" ]]; then
			echo "Please provide the tar file-name."
			exit -1
        fi
		echo 'Extracting files...'
		do_restore_files $2
		;;

	restore-db)
		restore_check $@
		dumpfile=$2

		echo "$*" |grep -q '[-]force' || {
			askcontinue "Deletes database $DBNAME!"
		}

		if [[ "$RUN_POSTGRES" == "1" ]]; then
			do_restore_db_in_docker_container $dumpfile
		else
			askcontinue "Trying to restore database on remote database. Please make sure, that the user $DB_USER has enough privileges for that."
			do_restore_db_on_external_postgres $dumpfile
		fi
		set_db_ownership

		;;

    restore)

        if [[ ! -f $2 ]]; then
            echo "File $2 not found!"
            exit -1
        fi
        if [[ -n "$3" && ! -f $3 ]]; then
            echo "File $3 not found!"
            exit -1
        fi

		dumpfile=$2
		tarfiles=$3

		$0 restore-db $dumpfile
		
		if [[ "$tarfiles" == "[-]force" ]]; then
			tarfiles=""
		fi

        if [[ -n "$tarfiles" ]]; then
			$0 restore-files $tarfiles
        fi

        echo "Restart systems by $0 restart"
        ;;
    restore-dev-db)
		if [[ "$ALLOW_RESTORE_DEV" ]]; then
			echo "ALLOW_RESTORE_DEV must be explicitly allowed."
			exit -1
		fi
        echo "Restores dump to locally installed postgres and executes to scripts to adapt user passwords, mailservers and cronjobs"
		restore_check $@
		$0 restore-db $ALL_PARAMS
		$0 turn-into-dev $ALL_PARAMS

        ;;
	turn-into-dev)
		if [[ "$DEVMODE" != "1" ]]; then
			echo "When applying this sql scripts, the database is not usable anymore for production environments. "
			echo "Please set DEVMODE=1 to allow this"
			exit -1
		fi
        SQLFILE=machines/postgres/turndb2dev.sql
		$0 psql < $SQLFILE
		
		;;
	psql)
		# execute psql query

		sql=$(
		while read line
		do
			echo "$line"
		done < "${2:-/dev/stdin}"
		)

		if [[ "$RUN_POSTGRES" == "1" ]]; then
			$dcrun postgres psql $2
		else
			export PGPASSWORD=$DB_PWD
			echo "$sql" | psql -h $DB_HOST -p $DB_PORT -U $DB_USER -w $DBNAME
		fi 
		;;

    springclean)
        docker system prune

        echo removing dead containers
        docker rm $(docker ps -a -q)

        echo Remove untagged images
        docker images | grep "<none>" | awk '{ print "docker rmi " $3 }' | bash

        echo "delete unwanted volumes (can pass -dry-run)"
        docker rmi $(docker images -q -f='dangling=true')
        ;;
    up)
		set_db_ownership
        $dc up $ALL_PARAMS
        ;;
    debug)
		# puts endless loop into container command and then attaches to it;
		# by this, name resolution to the container still works
        if [[ -z "$2" ]]; then
            echo "Please give machine name as second parameter e.g. postgres, odoo"
            exit -1
        fi
		set_db_ownership
        echo "Current machine $2 is dropped and restartet with service ports in bash. Usually you have to type /debug.sh then."
        askcontinue
        # shutdown current machine and start via run and port-mappings the replacement machine
        $dc kill $2
        cd $DIR
		DEBUGGING_COMPOSER=$DIR/run/debugging.yml
		cp $DIR/config/debugging/template.yml $DEBUGGING_COMPOSER
		sed -i -e "s/\${DCPREFIX}/$DCPREFIX/" -e "s/\${NAME}/$2/" $DEBUGGING_COMPOSER
		dc="$dc -f $DEBUGGING_COMPOSER"  # command now has while loop

        #execute self
		$dc up -d $2
		$0 attach $2 

        ;;
    attach)
        if [[ -z "$2" ]]; then
            echo "Please give machine name as second parameter e.g. postgres, odoo"
            exit -1
        fi
		display_machine_tips $2
        $dc exec $2 bash
        ;;
    runbash)
		set_db_ownership
        if [[ -z "$2" ]]; then
            echo "Please give machine name as second parameter e.g. postgres, odoo"
            exit -1
        fi
		display_machine_tips $2
        $dc run $2 bash
        ;;
    rebuild)
        cd $DIR/machines/odoo
        cd $DIR
        eval "$dc build --no-cache $2"
        ;;
    build)
        cd $DIR
        eval "$dc build $ALL_PARAMS"
        ;;
    kill)
        cd $DIR
        eval "$dc kill $2 $3 $4 $5 $6 $7 $8 $9"
        ;;
    stop)
        cd $DIR
        eval "$dc stop $2 $3 $4"
        ;;
    logsn)
        cd $DIR
        eval "$dc logs --tail=$2 -f -t $3 $4"
        ;;
    logs)
        cd $DIR
        lines="${@: -1}"
        if [[ -n ${lines//[0-9]/} ]]; then
            lines="5000"
        else
            echo "Showing last $lines lines"
        fi
        eval "$dc logs --tail=$lines -f -t $2 "
        ;;
    logall)
        cd $DIR
        eval "$dc logs -f -t $2 $3"
        ;;
    rm)
        cd $DIR
        $dc rm $ALL_PARAMS
        ;;
    restart)
        cd $DIR
        eval "$dc kill $2"
        eval "$dc up -d $2"
        ;;
    install-telegram-bot)
        pip install python-telegram-bot
        ;;
	telegram-setup)
		echo
		echo 1. Create a new bot and get the Token
		read -p "Now enter the token [$TELEGRAMBOTTOKEN]:" token
		if [[ -z "$token" ]]; then
			token=$TELEGRAMBOTTOKEN
		fi
		if [[ -z "$token" ]]; then

			exit 0
		fi
		echo 2. Create a new public channel, add the bot as administrator and users
		read -p "Now enter the channel name with @:" channelname
		if [[ -z "$channelname" ]]; then
			exit 0
		fi
        python $DIR/bin/telegram_msg.py "__setup__" $token $channelname
		echo "Finished - chat id is stored; bot can send to channel all the time now."
		;;
    purge-source)
        $dcrun odoo rm -Rf /opt/openerp/customs/$CUSTOMS
        ;;
    update-source)
		$dcrun source_code /sync_source.sh $2
        ;;
    update)
        echo "Run module update"
		if [[ -n "$ODOO_UPDATE_START_NOTIFICATION_TOUCH_FILE" ]]; then
			date +%s > $ODOO_UPDATE_START_NOTIFICATION_TOUCH_FILE
		fi
        if [[ "$RUN_POSTGRES" == "1" ]]; then
			$dc up -d postgres
        fi
        $dc kill odoo_cronjobs # to allow update of cronjobs (active cronjob, cannot update otherwise)
        $dc kill odoo_update
        $dc rm -f odoo_update
        $dc up -d postgres && sleep 3

        set -e
        # sync source
        $dcrun source_code
        set +e

        $dcrun odoo_update /update_modules.sh $2
        $dc kill odoo nginx
        if [[ "$RUN_ASTERISK" == "1" ]]; then
            $dc kill ari stasis
        fi
        $dc kill odoo
        $dc rm -f
        $dc up -d
        python $DIR/bin/telegram_msg.py "Update done" &> /dev/null
        echo 'Removing unneeded containers'
        $dc kill nginx
        $dc up -d
        df -h / # case: after update disk / was full
		if [[ -n "$ODOO_UPDATE_START_NOTIFICATION_TOUCH_FILE" ]]; then
			echo '0' > $ODOO_UPDATE_START_NOTIFICATION_TOUCH_FILE
		fi

       ;;
    make-CA)
        echo '!!!!!!!!!!!!!!!!!!'
        echo '!!!!!!!!!!!!!!!!!!'
        echo '!!!!!!!!!!!!!!!!!!'
        echo
        echo
        echo "Extreme Caution!"
        echo 
        echo '!!!!!!!!!!!!!!!!!!'
        echo '!!!!!!!!!!!!!!!!!!'
        echo '!!!!!!!!!!!!!!!!!!'

        askcontinue -force
        export dc=$dc
        $dc kill ovpn
        $dcrun ovpn_ca /root/tools/clean_keys.sh
        $dcrun ovpn_ca /root/tools/make_ca.sh
        $dcrun ovpn_ca /root/tools/make_server_keys.sh
        $dc rm -f
        ;;
    make-keys)
        export dc=$dc
        bash $DIR/config/ovpn/pack.sh
        $dc rm -f
        ;;
    export-i18n)
        LANG=$2
        MODULES=$3
        if [[ -z "$MODULES" ]]; then
            echo "Please define at least one module"
            exit -1
        fi
        rm $DIR/run/i18n/* || true
        chmod a+rw $DIR/run/i18n
        $dcrun odoo_lang_export /export_i18n.sh $LANG $MODULES
        # file now is in $DIR/run/i18n/export.po
        ;;
    import-i18n)
        $dcrun odoo /import_i18n.sh $ALL_PARAMS
        ;;
	sanity_check)
		sanity_check
		;;
    *)
        echo "Invalid option $1"
        exit -1
        ;;
    esac
}


function cleanup() {

    if [[ -f config/docker-compose.yml ]]; then
        /bin/rm config/docker-compose.yml || true
    fi

	cd $DIR
	if [[ ! -z "$ALTERNATE_DOCKERFILE_NAME" ]]; then
		find machines -name "$ALTERNATE_DOCKERFILE_NAME" -delete
	fi
}

function try_to_set_owner() {
	OWNER=$1
	dir=$2
	if [[ "$(stat -c "%u" "$dir")" != "$OWNER" ]]; then
		echo "Trying to set correct permissions on restore directory"
		cmd="chown $OWNER $dir"
		$cmd || {
			sudo $cmd
		}
	fi
}

function sanity_check() {
    if [[ ( "$RUN_POSTGRES" == "1" || -z "$RUN_POSTGRES" ) && "$DB_HOST" != 'postgres' ]]; then
        echo "You are using the docker postgres container, but you do not have the DB_HOST set to use it."
        echo "Either configure DB_HOST to point to the docker container or turn it off by: "
        echo 
        echo "RUN_POSTGRES=0"
        exit -1
    fi

    if [[ "$RUN_POSTGRES" == "1"  ]]; then
		RESTORE_DIR="$DIR/run/restore"
		if [[ -d "$RESTORE_DIR" ]]; then
			try_to_set_owner "1000" "$RESTORE_DIR"
		fi
	fi

	if [[ -d $ODOO_FILES ]]; then
		# checking directory permissions of session files and filestorage
		try_to_set_owner "1000" "$ODOO_FILES"
	fi

	# make sure the odoo_debug.txt exists; otherwise directory is created
	if [[ ! -f "$DIR/run/odoo_debug.txt" ]]; then
		touch $DIR/run/odoo_debug.txt
	fi

	if [[ -z "ODOO_MODULE_UPDATE_DELETE_QWEB" ]]; then
		echo "Please define ODOO_MODULE_UPDATE_DELETE_QWEB"
		echo "Whenever modules are updated, then the qweb views are deleted."
		echo
		echo "Typical use for development environment."
		echo
		exit -1
	fi

	if [[ -z "ODOO_MODULE_UPDATE_RUN_TESTS" ]]; then
		echo "Please define wether to run tests on module updates"
		echo
		exit -1
	fi

	if [[ -z "$ODOO_CHANGE_POSTGRES_OWNER_TO_ODOO" ]]; then
		echo "Please define ODOO_CHANGE_POSTGRES_OWNER_TO_ODOO"
		echo In development environments it is safe to set ownership, so
		echo that accidently accessing the db fails
		echo
		exit -1
	fi
}

function set_db_ownership() {
	# in development environments it is safe to set ownership, so
	# that accidently accessing the db fails
	if [[ -n "$ODOO_CHANGE_POSTGRES_OWNER_TO_ODOO" ]]; then
		if [[ "$RUN_POSTGRES" == "1" ]]; then
			$dc up -d postgres
			$dcrun odoo bash -c "cd /opt/openerp/admin/module_tools; python -c\"from module_tools import set_ownership_exclusive; set_ownership_exclusive()\""
		else
			bash <<-EOF
			cd $ODOO_HOME/data/src/admin/module_tools
			python -c"from module_tools import set_ownership_exclusive; set_ownership_exclusive()"
			EOF
		fi
	fi
	set +x
}

function display_machine_tips() {

	tipfile=$DIR/machines/$1/tips.txt
	if [[ -f "$tipfile" ]]; then
		echo 
		echo Please notice:
		echo ---------------
		echo
		cat $tipfile
		echo 
		echo
		sleep 1
	fi

}

function replace_params_in_dockerfiles() {
	# replaces params in Dockerfile, that docker usually does not
	set -x
	ALTERNATE_DOCKERFILE_NAME='.Dockerfile'
	cd $DIR
	for file in $(find machines -name "Dockerfile")
	do
		cd $DIR
		cd $(dirname $file)
		cp Dockerfile $ALTERNATE_DOCKERFILE_NAME
		replace_all_envs_in_file $ALTERNATE_DOCKERFILE_NAME

	done

	set -x
	cd $DIR/run
	for file in $(ls *.yml)
	do
		echo $file
		python <<-EOF
		from yaml import load, dump
		with open("$file", 'r') as f:
		    yml = load(f.read())

		services = yml.get('services', {})
		for item in services.values():
		    if item.get('build', False) and isinstance(item['build'], (str, unicode)):
		        item['build'] = {
				    'context': item['build'],
		            'dockerfile': "$ALTERNATE_DOCKERFILE_NAME",
		        }
		with open("$file", "w") as f:
		    f.write(dump(yml))
		EOF
	done

	cd $DIR
}

function main() {
	startup
	default_confs
	export_settings
	prepare_filesystem
	prepare_yml_files_from_template_files
	replace_params_in_dockerfiles
	sanity_check
	export odoo_manager_started_once=1
	do_command "$@"
	cleanup

}
main $@



