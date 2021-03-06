define('app/views/key_list', [
    'text!app/templates/key_list.html',
    'ember'
    ],
    /**
     *
     * Key list page
     *
     * @returns Class
     */
    function(key_list_html) {
        return Ember.View.extend({
            tagName: false,

            init: function() {
                this._super();
                this.set('template', Ember.Handlebars.compile(key_list_html));
            },

            setDefaultKey: function(){
                var key = this.getSelectedKeys();

                if(key.length == 0){
                    return;
                } else if(key.length > 1){
                    alert('You can only set one key as the deafult');
                    return;
                }
                
                key[0].setDefaultKey();
                $('#keys .keys-footer').fadeOut(200);
            },

            deleteKey: function(){
                var keys = this.getSelectedKeys();
                var plural = false;

                if(keys.length == 0){
                    return;
                } else if(keys.length > 1){
                    plural = true;
                }

                Mist.confirmationController.set("title", 'Delete Key' + (plural ? 's' : ''));

                var names = '';

                keys.forEach(function(key){
                    names = names + ' ' + key.name;
                });

                Mist.confirmationController.set("text", 'Are you sure you want to delete' +
                        names +'?');

                Mist.confirmationController.set("callback", function(){
                    keys.forEach(function(key){
                       key.deleteKey();
                       window.history.go(-1);
                       $('#keys .keys-footer').fadeOut(200);
                    });
                });

                Mist.confirmationController.set("fromDialog", true);
                Mist.confirmationController.show();
            },

            getSelectedKeys: function(){
                var keys = new Array();

                Mist.keysController.forEach(function(key){
                        if(key.selected){
                            keys.push(key);
                        }
                });

                return keys;
            },
        });
    }
);
