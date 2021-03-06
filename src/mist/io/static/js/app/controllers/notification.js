define('app/controllers/notification', [
    'ember',
    'jquery',
    'mobile'
    ],
    /**
     * Notification controller
     *
     * @returns Class
     */
    function() {
        return Ember.Object.extend({

            timeout: false,

            notify: function(message){
                if(this.timeout){
                    clearTimeout(this.timeout);
                }
                log("notification: " + message);
                $.mobile.loading( 'show', {
                            text: message,
                            textVisible: true,
                            textonly: true,
                            theme: $.mobile.pageLoadErrorMessageTheme
                });
                this.timeout = setTimeout("$.mobile.loading( 'hide' )", 1500);
            }
        });
    }
);
