var Chatify = (function (Chatify, moment, $) {
  'use strict';

  var bus = new Vue();

  Vue.component('pfi-user', {
    props: ['user'],
    template: `
            <li class='nav-item'>
                <a class='nav-link text-dark' href="#"
                    @click.prevent="selectUser(user)"
                >
                    <i class="fa"
                        :class="{ 'text-green fa-check-circle': user.online, 'text-muted fa-check-circle-o' : !user.online }"
                    ></i>
                    <span :class="{ 'text-bold': user.online }">{{ user.name }}</span> (<span class="small text-muted">{{ user.ip_address }}</span>)
                    <span v-if="unreadMessages > 0" class="pull-right badge bg-blue">{{ unreadMessages }}</span></a>
            </li>
        `,
    data: function () {
      return {};
    },
    computed: {
      unreadMessages: function () {
        return this.user.conversations.length > 0
          ? this.user.conversations.filter(function (c) {
              return c.status == 0 && c.recipient_admin;
            }).length
          : this.user.unread;
      },
    },
    methods: {
      selectUser: function (user) {
        bus.$emit('open_conversation', user);
      },
    },
  });

  Vue.component('pfi-users', {
    props: ['users'],
    template: `
            <div class='p-0'>
                <div class="p-2"><input v-model="search" class="form-control input-block" placeholder="Type a user" /></div>
                <ul style="height: 200px; overflow-y: auto;" class="nav flex-column">
                    <pfi-user
                        v-for="user in filteredUsers"
                        :user="user"
                    ></pfi-user>
                </ul>
            </div>
        `,
    data: function () {
      return {
        search: '',
      };
    },
    computed: {
      filteredUsers: function () {
        var search = this.search;
        var filtered = [];
        for (var k in this.users) {
          if (
            this.users[k].name.toLowerCase().indexOf(search.toLowerCase()) > -1
          ) {
            filtered.push(this.users[k]);
          }
        }

        filtered.sort(function (a, b) {
          if (a.online && !b.online) return -1;
          if (!a.online && b.online) return 1;
          return 0;
        });

        return filtered;
      },
    },
    methods: {},
    mounted: function () {},
  });
  Vue.component('pfi-chat-all', {
    template: `
        <div class='card-body p-2'>
            <form @submit.prevent="sendMessage" >
                <div class="form-group mb-0">
                    <label class="form-control-label">Send to All</label>
                    <textarea v-model="message" rows="8" name="message" autocomplete="off" placeholder="Type Message ..." class="form-control">
                    </textarea>
                </div>
                <div class="mt-1 btn-group btn-group-sm d-flex">
                    <button type="submit" class="btn bg-green btn-flat"><i class="fa fa-send"></i> Send</button>
                    <button @click="cancel()" type="submit" class="btn bg-orange btn-flat"><i class="fa fa-send"> Cancel</i></button>
                </div>
            </form>
        </div>
        `,
    data: function () {
      return {
        message: '',
      };
    },
    computed: {},
    methods: {
      sendMessage: function () {
        this.$emit('send', this.message);
      },
      cancel: function () {
        this.$emit('cancel');
      },
    },
    mounted: function () {},
  });

  Vue.component('pfi-conversation', {
    props: ['conversation', 'channel'],
    template: `
            <div class="direct-chat-msg"
                :class="{ 'right': conversation.sender_admin }"
            >
                <div class="direct-chat-info clearfix">
                    <span class="direct-chat-name"
                        :class="{ 'pull-right': conversation.sender_admin, 'pull-left': !conversation.sender_admin }"
                    >
                        {{ conversation.sender }}
                    </span>

                </div>
                <!-- /.direct-chat-info -->
                <img class="direct-chat-img" src="/img/default.png" alt="Message User Image">
                <!-- /.direct-chat-img -->
                <div
                 class="direct-chat-text">
                    {{ conversation.message }}

                </div>
                <button
                    class="btn btn-link btn-xs"
                    style="position: relative; margin: 0 .3em; height: 1.5em; border: 1px solid #eee;"
                    @mouseover="showOptions = true"
                    @mouseleave="showOptions = false"
                    :class="{ 'pull-left': conversation.sender_admin, 'pull-right': !conversation.sender_admin }"
                >
                    <i class="fa fa-ellipsis-h text-black"
                    ></i>
                        <ul
                            style="position:absolute; top: .7em;"
                            v-if="showOptions" class="dropdown-menu" :style="{ display: showOptions ? 'block' : 'none' }"
                        >
                            <li><a href="#" @click.prevent="deleteConversation(conversation)"><i class="fa fa-fw fa-trash-o text-red"></i>Delete</a></li>
                        </ul>
                </button>
                <div class="clearfix">
                    <span class="direct-chat-timestamp small"
                        :class="{ 'pull-left': conversation.sender_admin, 'pull-right': !conversation.sender_admin }"
                    >
                        {{ getTimestamp() }}
                    </span>
                </div>


                <!-- /.direct-chat-text -->
            </div>
        `,
    data: function () {
      return {
        showOptions: false,
      };
    },
    methods: {
      getTimestamp: function () {
        return moment
          .unix(this.conversation.timestamp)
          .format('YYYY-MM-DD HH:mm');
      },
      deleteConversation: function (conversation) {
        bus.$emit('delete_conversation', this.channel, conversation);
      },
    },
    mounted: function () {},
  });

  Vue.component('pfi-chat-manager', {
    props: ['clients'],
    template: `
                <div style="position: fixed; bottom: 1em; right: 1em; z-index: 999" class="pfi-manager">
                    <div class="card card-widget widget-user-2" v-if="expanded"
                        style="card-shadow: 0 0 5px rgba(0,0,0,.5); margin-right: 1.5em; margin-bottom: 5px; background: #fff; border-radius: 1em 1em 0 1em;"
                        :style="{ 'width': width, 'height': selectedConvo != null ? height : '300px' }"
                        class="chat-card elevation-4">
                        <div style='z-index: 9999;' class="card card-widget">
                            <!-- Add the bg color to the header using any of the bg-* classes -->
                            <div class="card-header with-border">
                                <h3 class="card-title"><i class="fa fa-fw fa-comments text-green"></i>Chat</h3>
                                <div class="card-tools pull-right">
                                    <span v-if="unreadMessages > 0" data-toggle="tooltip" class="badge bg-light-blue" >{{ unreadMessages }}</span>
                                    <button @click="toggleExpand()" type="button" class="btn btn-tool"><i class="fa fa-close"></i></button>
                                </div>
                            </div>
                            <div
                                v-if="selectedConvo"
                                style="positon: relative;"
                                class="card-body"
                            >
                                <div style="border-bottom: 1px solid #eee; padding: 0 .5em .5em 0; position: relative;">
                                    <span
                                    style="border-radius: 50%;"
                                    @click="backToList()"
                                    class="btn btn-xs bg-blue">
                                    <i title="Back to List" class="fa fa-chevron-left"></i></span>
                                    From: <span
                                            :title="selectedConvo.online ? 'User is ONLINE' : 'User is OFFLINE'"
                                            :class="{ 'bg-green': selectedConvo.online, 'bg-gray': !selectedConvo.online }" class="badge"
                                           >
                                                <i
                                                    :class="{ 'fa-check-circle': selectedConvo.online, 'fa-chain-broken': !selectedConvo.online }"
                                                    class="fa fa-fw"></i>
                                                {{ selectedConvo.name }}
                                        </span>
                                        <span @click="clearMessages(selectedConvo)" style="cursor: pointer;" class="pull-right text-red"><i class="fa fa-fw fa-trash-o"></i>CLEAR</span>
                                </div>
                                <div id="messages" class="direct-chat-messages">
                                    <div v-if="loadingConvo" class="alert bg-blue text-center text-whtie">Loading Conversations...</div>
                                    <pfi-conversation
                                        v-if="!loadingConvo"
                                        v-for="convo in selectedConvo.conversations"
                                        :conversation="convo"
                                        :channel="selectedConvo.channel"
                                    ></pfi-conversation>
                                </div>
                            </div>
                            <div v-if="!chatAll" class="card-footer p-0" style="overflow-y: auto;">
                                <pfi-users
                                    v-if="!selectedConvo"
                                    :users="clients"
                                ></pfi-users>
                                <div v-if="selectedConvo" class="p-2">
                                    <form @submit.prevent="sendMessage" >
                                        <div class="input-group">
                                        <input v-model="message" type="text" name="message" autocomplete="off" placeholder="Type Message ..." class="form-control">
                                            <span class="input-group-btn">
                                                <button type="submit" class="btn bg-green btn-flat"><i class="fa fa-send"></i></button>
                                            </span>
                                        </div>
                                    </form>
                                </div>

                            </div>
                            <pfi-chat-all v-if="chatAll" @cancel="cancelSendToAll()" @send="sendToAll"></pfi-chat-all>
                            <div v-if="!chatAll && !selectedConvo" class='p-0 container-fluid'>
                                <button @click="chatAll = true" class='btn btn-flat btn-block btn-primary btn-sm'>Chat All</button>
                            </div>
                        </div>
                    </div>
                    <button @click="toggleExpand()"
                        style="border-radius: 50%; card-shadow: 0 0 5px rgba(0,0,0,.7); outline: none; float: right; position: relative;"
                        class="btn bg-green border border-white elevation-4">
                            <i :class="{ 'fa-comments-o': expanded, 'fa-comments': !expanded }" class="fa fa-2x"></i>
                            <span v-if="unreadMessages > 0" style="position: absolute;" class="badge bg-maroon">{{ unreadMessages }}</span>
                    </button>
                </div>
            `,
    data: function () {
      return {
        expanded: false,
        message: '',
        selectedConvo: null,
        loadingConvo: false,
        chatAll: false,
        width: '300px',
        height: '385px',
      };
    },
    computed: {
      unreadMessages: function () {
        var unread = 0;
        for (var k in this.clients) {
          unread +=
            this.clients[k].conversations.length > 0
              ? this.clients[k].conversations.filter(function (m) {
                  return m.status == 0 && m.recipient_admin;
                }).length
              : this.clients[k].unread;
        }
        return unread;
      },
    },
    methods: {
      toggleExpand: function () {
        this.expanded = !this.expanded;
        if (this.expanded) {
          this.calculateSize();
        }
      },
      cancelSendToAll: function () {
        this.chatAll = false;
      },
      sendToAll: function (message) {
        console.log('Sending to All', message);
        if (message.trim().length > 0) {
          bus.$emit('sendmessage.all', message);
        }
      },
      backToList: function () {
        var unread = this.selectedConvo.conversations.filter(function (c) {
          return !c.status && c.recipient_admin;
        });
        if (unread.length > 0) {
          var ids = unread.map(function (u) {
            return u.id;
          });
          bus.$emit('markasread', this.selectedConvo.channel, ids);
        }
        this.selectedConvo = null;
      },
      sendMessage: function () {
        var self = this;
        if (this.selectedConvo) {
          if (this.message.trim().length > 0) {
            bus.$emit('sendmessage', this.selectedConvo, this.message);
          }
        }
      },
      clearMessages: function (user) {
        if (confirm('Are you sure?')) {
          bus.$emit('clear_messages', user);
        }
      },
      calculateSize: function () {
        var winW = window.screen.width;
        var w = '300px';
        if (winW < 340) {
          w = '250px';
        } else if (winW > 360 && winW < 380) {
          w = winW - 100 + 'px';
        } else if (winW > 380 && winW < 400) {
          w = winW - 90 + 'px';
        }

        var winH = window.screen.height;
        var h = '385px';
        if (winH < 340) {
          h = '330px';
        } else if (winH > 360 && winH < 500) {
        }

        this.width = w;
        this.height = h;
      },
    },
    created: function () {
      var self = this;
      bus.$on('open_conversation', function (user) {
        self.selectedConvo = user;
        if (self.selectedConvo) {
          self.loadingConvo = true;
          $.getJSON(
            '/adminapi/chat/conversations?user=' +
              encodeURIComponent(user.client_id)
          )
            .done(function (response) {
              self.selectedConvo.conversations = response.conversations;
              setTimeout(function () {
                var container = self.$el.querySelector('#messages');
                container.scrollTop = container.scrollHeight;

                var unread = self.selectedConvo.conversations.filter(function (
                  c
                ) {
                  return !c.status && c.recipient_admin;
                });
                if (unread.length > 0) {
                  var ids = unread.map(function (u) {
                    return u.id;
                  });
                  bus.$emit('markasread', self.selectedConvo.channel, ids);
                }
              }, 100);
            })
            .fail(function (err) {})
            .always(function () {
              self.loadingConvo = false;
            });
        }
      });

      bus.$on('messagesent', function () {
        self.message = '';
      });
      bus.$on('messagesent.all', function () {
        self.chatAll = false;
        alert('Message has been sent to all users');
      });
    },
    mounted: function () {},
  });

  var Chatify = Chatify || {};

  Chatify.app = null;

  var init = function (options) {
    var el = document.querySelector(options.el);
    var notification = options.notifcation || null;
    try {
      if (notification) {
        notification = new Audio(notification);
      }
    } catch (ex) {
      notification = null;
    }
    if (!el) {
      throw 'Element must be a valid DOM element';
    }
    Chatify.app = new Vue({
      el: options.el,
      data: function () {
        return {
          ws: false,
          wsRetries: 0,
          csrf_name: options.csrf_name,
          csrf_value: options.csrf_value,
          domain: options.domain,
          notifcation: notification,
          clients: {},
        };
      },
      watch: {
        ws: function (curr, prev) {
          if (!curr) {
          }
        },
      },
      methods: {
        init: function () {
          this.$refs.chat.style.display = 'inherit';
          this.subscribe();
          var self = this;
          this.getAccounts();
        },
        getWsUrl: function () {
          var ipformat =
            /^(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$/;
          var url =
            window.location.hostname == this.domain
              ? '10.0.0.1'
              : window.location.hostname;
          var scheme = window.location.href.indexOf("https") > -1 ? "wss" : "ws";
          var wsUrl = scheme + '://' + url + '/ws';
          if (url.match(ipformat)) {
            wsUrl = scheme + '://' + url + ':8080';
          }
          return wsUrl;
        },
        connectToWs: function () {
          var self = this;
          this.ws = new ab.Session(
            this.getWsUrl(),
            function () {
              self.loading = false;
              self.init();
              self.wsRetries = 0;
            },
            function () {
              self.loading = true;
              setTimeout(function () {
                self.wsRetries++;
                if (self.wsRetries <= 5) {
                  self.connectToWs();
                } else {
                  alert("Can't Connect to Server");
                }
              }, 5000);
            }
          );
        },
        subscribe: function () {
          this.ws.subscribe('onchatreceive.admin', this.onChatReceive);
          this.ws.subscribe('onclientchatconnect', this.onClientChatConnect);
          this.ws.subscribe(
            'onclientchatdisconnect',
            this.onClientChatDisconnect
          );
        },
        onChatReceive: function (topic, data) {
          var data = data.data;
          var hash = data.sender_hash;
          if (typeof this.clients[hash] !== 'undefined') {
            if (this.notifcation) {
              try {
                if (notification.currentTime) {
                  notification.currentTime = 0;
                  notification.play();
                  setTimeout(functino);
                }
              } catch (ex) {}
            }
            var conversations = this.clients[hash].conversations;
            if (conversations.indexOf(data) == -1) {
              conversations.push(data);
              var self = this;
              setTimeout(function () {
                var container = self.$el.querySelector('#messages');
                if (container) {
                  container.scrollTop = container.scrollHeight;
                }
              }, 100);
            }
          }
        },
        onClientChatConnect: function (topic, data) {
          var ips = data.data.ips;
          for (var k in this.clients) {
            if (ips.indexOf(this.clients[k].channel) > -1) {
              this.clients[k].online = true;
            }
          }
        },
        onClientChatDisconnect: function (topic, data) {
          var ips = data.data.ips;
          for (var k in this.clients) {
            if (ips.indexOf(this.clients[k].channel) > -1) {
              this.clients[k].online = false;
            }
          }
        },
        getAccounts: function () {
          var self = this;
          $.getJSON('/adminapi/chat/accounts')
            .done(function (response) {
              self.csrf_name = response.token.csrf_name;
              self.csrf_value = response.token.csrf_value;
              self.clients = response.accounts;
            })
            .fail(function (err) {});
        },
      },
      created: function () {
        var self = this;
        bus.$on('sendmessage', function (client, message) {
          self.ws
            .call('sendchatmessage.admin', {
              sender: 'http_admin_protocol',
              recipient: client.client_id,
              message: message,
            })
            .then(
              function (result) {
                if (result.status == 'OK') {
                  client.conversations.push(result.data);
                  bus.$emit('messagesent');
                } else {
                }
                bus.$emit('messagesent');
                setTimeout(function () {
                  var container = self.$el.querySelector('#messages');
                  container.scrollTop = container.scrollHeight;
                }, 100);
              },
              function (error) {
                // call failed
              }
            );
        });
        bus.$on('sendmessage.all', function (message) {
          self.ws
            .call('sendchatmessage.admin', {
              sender: 'http_admin_protocol',
              recipient: 'all',
              message: message,
            })
            .then(
              function (result) {
                console.log('result', result);
                if (result.status == 'OK') {
                } else {
                }
                bus.$emit('messagesent.all');
              },
              function (error) {
                // call failed
              }
            );
        });
        bus.$on('delete_conversation', function (hash, conversation) {
          var convos = self.clients[hash].conversations;
          var index = convos.indexOf(conversation);

          if (index > -1) {
            var data = {
              csrf_name: self.csrf_name,
              csrf_value: self.csrf_value,
              chatid: self.chatid,
              id: conversation.id,
            };
            $.ajax({
              url: '/adminapi/chat/message',
              type: 'DELETE',
              data: data,
              dataType: 'json',
            })
              .done(function (output) {
                self.csrf_name = output.token.csrf_name;
                self.csrf_value = output.token.csrf_value;
                if (output.result.status == 'OK') {
                  convos.splice(index, 1);
                }
              })
              .fail(function (err) {})
              .always(function () {});
          }
        });
        bus.$on('clear_messages', function (user) {
          var data = {
            csrf_name: self.csrf_name,
            csrf_value: self.csrf_value,
            user: user.client_id,
          };
          $.ajax({
            url: '/adminapi/chat/messages',
            type: 'DELETE',
            data: data,
            dataType: 'json',
          })
            .done(function (output) {
              self.csrf_name = output.token.csrf_name;
              self.csrf_value = output.token.csrf_value;
              if (output.result.status == 'OK') {
                user.conversations = [];
                user.unread = 0;
              }
            })
            .fail(function (err) {})
            .always(function () {});
        });

        bus.$on('markasread', function (hash, ids) {
          var data = {
            csrf_name: self.csrf_name,
            csrf_value: self.csrf_value,
            ids: ids,
          };
          $.ajax({
            url: '/adminapi/chat/markasread',
            type: 'POST',
            data: data,
            dataType: 'json',
          })
            .done(function (output) {
              self.csrf_name = output.token.csrf_name;
              self.csrf_value = output.token.csrf_value;
              if (output.result.status == 'OK') {
                var convos = self.clients[hash].conversations.map(function (c) {
                  if (ids.indexOf(c.id) > -1) {
                    c.status = 1;
                  }
                });
              }
            })
            .fail(function (err) {})
            .always(function () {});
        });
      },
      mounted: function () {
        this.connectToWs();
      },
    });
  };

  return {
    init: init,
  };
})(Chatify || {}, moment, jQuery);
