define('app/views/machine', [
    'text!app/templates/machine.html','ember', 'd3', 'cubism'],
    /**
     *
     * Machine page
     *
     * @returns Class
     */
    function(machine_html) {
        return Ember.View.extend({
            tagName: false,
            machineBinding: 'Mist.machine',

            disabledShellClass: function() {
                if (this.machine && this.machine.hasKey && this.machine.state == 'running') {
                    return '';
                } else {
                    return 'ui-disabled';
                }
            }.property('machine.hasKey'),

            disabledTagClass: function() {
                if (this.machine && this.machine.can_tag) {
                    return '';
                } else {
                    return 'ui-disabled';
                }
            }.property('machine.can_tag'),

            disabledPowerClass: function() {
                if (this.machine && this.machine.state === 'terminated') {
                    return 'ui-disabled';
                } else {
                    return '';
                }
            }.property('machine.state'),

            metadata: function() {
                if (!this.machine || !this.machine.extra) {
                    return [];
                }
                var ret = new Array();

                $.each(this.machine.extra, function(key, value) {
                    if (typeof(value) == 'string' || typeof(value) == 'number') {
                        ret.push({key:key, value: value});
                    }
                });
                return ret;
            }.property('machine'),

            machineKeys: function() {
                if (!this.machine) {
                    return [];
                }
                //return a list with the keys this server is associated to
                var keys = [];
                var that = this;
                Mist.keysController.content.forEach(function(key){
                    key.machines.forEach(function(item){
                        if (item[1] == that.machine.id) {
                            keys.push(key);
                        }
                    });
                });
                return keys
            }.property('machine'),

            keySelect: function(event, el){
                //Associates the selected key with the machine
                //TODO: turn parent to listview and refresh it properly
                Mist.keysController.associateKey($(event.target).val(), Mist.machine.backend.id, Mist.machine.id);
            },

            machineKeysRest: function() {
                if (!this.machine) {
                    return [];
                }
                //return a list with the names of all keys except the ones that are associated
                var keys = [];
                var that = this;
                var hasMachine = false;
                Mist.keysController.content.forEach(function(key){
                    hasMachine = false;
                    key.machines.forEach(function(item){
                        if (item[1] == that.machine.id) {
                            hasMachine = true;
                        }
                    });
                    if (hasMachine == false) {
                        keys.push(key);
                    }
                });
                return keys
            }.property('machine'),

            basicvars: function() {
                if (!this.machine) {
                    return [];
                }

                var publicIps = null;

                if ($.isArray(this.machine.public_ips)) {
                    publicIps = this.machine.public_ips.join();
                } else if (typeof this.machine.public_ips === 'string') {
                    publicIps = this.machine.public_ips;
                }

                var privateIps = null;

                if ($.isArray(this.machine.private_ips)) {
                    privateIps = this.machine.private_ips.join();
                } else if (typeof this.machine.private_ips === 'string') {
                    privateIps = this.machine.private_ips;
                }

                var basicvars = {
                        'Public IPs': publicIps,
                        'Private IPs': privateIps,
                        'DNS Name': this.machine.extra.dns_name,
                        'Launch Date': this.machine.extra.launchdatetime
                };

                if (this.machine.image && 'image' in this.machine &&
                        'name' in this.machine.image) {
                    basicvars['Image'] = this.machine.image.name;
                }

                var ret = new Array();

                $.each(basicvars, function(key, value) {
                    if (typeof(value) == 'string') {
                        ret.push({key:key, value: value});
                    }
                });

                return ret;

            }.property('machine'),

            name: function() {
                if (!this.machine) {
                    return '';
                }
                return this.machine.name || this.machine.id;
            }.property('machine'),

            upFor: function() {
                if (this.machine && this.machine.uptime) {
                    var ret = '';
                    var x = Math.floor(this.machine.uptime / 1000);

                    var seconds = x % 60;
                    x = Math.floor(x / 60);
                    var minutes = x % 60;
                    x = Math.floor(x / 60);
                    var hours = x % 24;
                    x = Math.floor(x / 24);
                    var days = x;

                    if (days) {
                        ret = ret + days + ' days, ';
                    }

                    if (hours) {
                        ret = ret + hours + ' hours, ';
                    }

                    if (minutes) {
                        ret = ret + minutes + ' minutes, ';
                    }

                    if (seconds) {
                        ret = ret + seconds + ' seconds';
                    } else {
                        ret = ret + '0 seconds';
                    }

                    return ret;

                } else {
                    return '';
                }
            }.property('machine.uptime'),

            providerIconClass: function() {
                if (!this.machine) {
                    return '';
                }
                return 'provider-' + this.machine.backend.provider;
            }.property('machine'),

            addRuleClicked: function() {
                Mist.rulesController.newRule();
                Ember.run.next(function(){
                    $('.rule-metric').last().selectmenu();
                    $('.rule-operator').last().selectmenu();
                    $('.rule-value').last().slider();
                    $('.rule-action').last().selectmenu();
                    $('.rules-container .delete-rule-button').last().button();
                });
            },

            setGraph: function() {

                Em.run.next(function() {
                    $('.monitoring-button').button();
                    $('#add-rule-button').button();
                    $('.rule-metric').selectmenu();
                    $('.rule-operator').selectmenu();
                    $('.rule-value').slider();
                    $('.rule-action').selectmenu();
                });

                if (!this.machine || !this.machine.hasMonitoring) {
                    if (this.context) {
                        this.context.stop();
                        $('#cpuGraph').empty();
                        $('#memoryGraph').empty();
                        $('#diskGraph').empty();
                        $('#networkGraph').empty();
                        $('#loadGraph').empty();
                    }
                    return;
                }

                var machine = this.machine;
                var that = this;

                machine.set('pendingStats', true);

                Em.run.next(function() {
                    var context = cubism.context()
                                        .serverDelay(0)
                                        .clientDelay(0)
                                        .step(5000)
                                        .size($(window).width() - 80);
                    Mist.context = context;

                    var localData = null;
                    var cores = null;
                    var networkInterfaces = null;
                    var disks = null;
                    var memoryTotal = false;
                    var loaded = false;

                    function drawCpu() {
                        return context.metric(function(start, stop, step, callback) {
                            start = +start;
                            stop = +stop;

                            if (machine.hasMonitoring) {
                                $.ajax({
                                    url: URL_PREFIX + '/backends/' + machine.backend.id +
                                         '/machines/' + machine.id + '/stats',
                                    type: 'GET',
                                    async: loaded,
                                    dataType: 'jsonp',
                                    data: {'start': (start / 1000),
                                           'stop': (stop / 1000),
                                           'step': step},
                                    timeout: 10000,
                                    success: function(data) {
                                        if (!data || !('cpu' in data)) {
                                            return callback(new Error('unable to load data'));
                                        } else {
                    			    loaded = true;
                                            localData = data;

                                            if (!cores) {
                                                cores = data['cpu']['cores'];
                                            }

                                            if (!networkInterfaces) {
                                                configureNetworkGraphs();
                                            }

                                            if (!disks) {
                                                configureDiskGraphs();
                                            }
                                        }
                                        machine.set('pendingStats', false);
                                    },
                                    error: function(jqXHR, textstate, errorThrown) {
                                        error('could not load monitoring data');
                                        //Mist.context.stop();
                                    }
                                });

                                if (localData && machine.hasMonitoring && cores) {
                                    return callback(null, localData['cpu']['utilization'].map(function(d) {
                                        return (d / cores) * 100;
                                    }));
                                } else {
                                    return callback(new Error('unable to load data'));
                                }

                            } else {
                                return callback(new Error('monitoring disabled'));
                            }
                        }, 'CPU');
                    }

                    function drawMemory() {
                        return context.metric(function(start, stop, step, callback) {
                            if (localData && machine.hasMonitoring && 'memory' in localData) {
                                if (!memoryTotal) {
                                    memoryTotal = localData['memory']['total'];
                                }
                                return callback(null, localData['memory']['used'].map(function(d) {
                                    return (d / memoryTotal) * 100;
                                }));
                            } else {
                                return callback(new Error('unable to load data'));
                            }
                        }, 'RAM');
                    }

                    function drawDisk(disk, ioMethod) {
                        return context.metric(function(start, stop, step, callback) {

                            if (localData && machine.hasMonitoring &&
                                'disk' in localData &&
                                ioMethod in localData.disk &&
                                disk in localData.disk[ioMethod] &&
                                'disk_ops' in localData.disk[ioMethod][disk]) {

                                return callback(null, localData['disk'][ioMethod][disk]['disk_ops'].map(function(d) {
                                    return d;
                                }));
                            } else {
                                return callback(new Error('unable to load data'));
                            }
                        }, 'DISK (' + disk + ', ' + ioMethod + ') ');
                    }

                    function drawLoad() {
                        return context.metric(function(start, stop, step, callback) {
                            if (localData && machine.hasMonitoring && 'load' in localData) {
                                return callback(null, localData['load'].map(function(d) {
                                    return d;
                                }));
                            } else {
                                return callback(new Error('unable to load data'));
                            }
                        }, 'LOAD ');
                    }

                    function drawNetwork(iface, stream) {
                        return context.metric(function(start, stop, step, callback) {

                            if (localData &&
                                machine.hasMonitoring &&
                                'network' in localData &&
                                iface in localData.network &&
                                stream in localData.network[iface]) {

                                return callback(null, localData['network'][iface][stream].map(function(d) {
                                    return d;
                                }));
                            } else {
                                return callback(new Error('unable to load data'));
                            }
                        }, 'NET (' + iface + ', ' + stream  + ') ');
                    }

                    function configureNetworkGraphs() {
                        networkInterfaces = [];
                        var data = [];
                        for (iface in localData['network']) {
                            networkInterfaces.push(iface);
                            data.push(drawNetwork(iface, 'tx'));
                            data.push(drawNetwork(iface, 'rx'));
                        }

                        d3.select('#networkGraph').call(function(div) {
                            div.selectAll('.horizon')
                               .data(data)
                               .enter()
                               .append('div')
                               .attr('class', 'horizon')
                               .call(context.horizon());
                            div.append('div').attr('class', 'rule').call(context.rule());
                        });
                    }

                    function configureDiskGraphs() {
                        disks = [];
                        data = [];

                        for (disk in localData['disk']['read']) {
                            disks.push(disk);
                            data.push(drawDisk(disk, 'read'));
                            data.push(drawDisk(disk, 'write'));
                        }

                        d3.select('#diskGraph').call(function(div) {
                            div.selectAll('.horizon')
                               .data(data)
                               .enter()
                               .append('div')
                               .attr('class', 'horizon')
                               .call(context.horizon());
                            div.append('div').attr('class', 'rule').call(context.rule());
                        });
                    }

                    var cpu = drawCpu();
                    var memory = drawMemory();
                    var load = drawLoad();

                    d3.select('#cpuGraph').call(function(div) {
                        div.append('div').attr('class', 'axis').call(context.axis().orient('top'));
                        div.selectAll('.horizon')
                           .data([cpu])
                           .enter()
                           .append('div')
                           .attr('class', 'horizon')
                           .call(context.horizon());
                        div.append('div').attr('class', 'rule').call(context.rule());
                    });

                    d3.select('#memoryGraph').call(function(div) {
                        div.selectAll('.horizon')
                           .data([memory])
                           .enter()
                           .append('div')
                           .attr('class', 'horizon')
                           .call(context.horizon());
                        div.append('div').attr('class', 'rule').call(context.rule());
                    });

                    d3.select('#loadGraph').call(function(div) {
                        div.selectAll('.horizon')
                           .data([load])
                           .enter()
                           .append('div')
                           .attr('class', 'horizon')
                           .call(context.horizon());
                        div.append('div').attr('class', 'rule').call(context.rule());
                    });

                    context.on('focus', function(i) {
                        d3.selectAll('.value').style('right', i == null ? null : context.size() - i + 'px');
                    });
                });

            }.observes('machine.hasMonitoring'),

            startStopContext: function(){
                if('context' in Mist){
                    if(Mist.graphPolling){
                        Mist.context.start();
                    } else {
                        Mist.context.stop();
                    }
                }
            }.observes('Mist.graphPolling'),

            handlePendingMonitoring: function() {
                if (this.machine && this.machine.pendingMonitoring) {
                    $('.pending-monitoring').show();
                    $('.monitoring-button').addClass('ui-disabled'); //.hide();
                } else {
                    $('.monitoring-button').removeClass('ui-disabled'); //.show();
                    $('.pending-monitoring').hide();
                }
            }.observes('machine.pendingMonitoring'),

            showShell: function() {
                setTimeout(function(){$('.shell-input input').focus()}, 1000);
            },

            init: function() {
                this._super();
                // cannot have template in home.pt as pt complains
                this.set('template', Ember.Handlebars.compile(machine_html));
            },
        });
    }
);
