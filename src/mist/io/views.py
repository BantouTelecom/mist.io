"""mist.io views"""
import os
import tempfile
import logging

from datetime import datetime

import requests

from hashlib import sha256

from Crypto.PublicKey import RSA

from pyramid.response import Response
from pyramid.view import view_config

from libcloud.compute.base import Node
from libcloud.compute.base import NodeSize
from libcloud.compute.base import NodeImage
from libcloud.compute.base import NodeLocation
from libcloud.compute.base import NodeAuthSSHKey
from libcloud.compute.deployment import MultiStepDeployment, ScriptDeployment, SSHKeyDeployment
from libcloud.compute.types import Provider

from mist.io.config import STATES
from mist.io.config import EC2_IMAGES
from mist.io.config import EC2_PROVIDERS
from mist.io.config import EC2_KEY_NAME
from mist.io.config import EC2_SECURITYGROUP
from mist.io.config import LINODE_DATACENTERS
from mist.io.config import SUPPORTED_PROVIDERS

from mist.io.helpers import connect
from mist.io.helpers import get_machine_actions
from mist.io.helpers import import_key, get_keypair, get_keypair_by_name
from mist.io.helpers import create_security_group
from mist.io.helpers import run_command
try:
    from mist.core.helpers import save_keypairs
except ImportError:
    from mist.io.helpers import save_keypairs
from mist.io.helpers import save_settings


log = logging.getLogger('mist.io')


@view_config(route_name='home', request_method='GET',
             renderer='templates/home.pt')
def home(request):
    """Gets all the basic data for backends, project name and session status.
    """
    try:
        email = request.environ['beaker.session']['email']
        session = True
    except:
        session = False
        try:
            email = request.registry.settings['email']
            password = request.registry.settings['password']
        except:
            email = ''

    core_uri = request.registry.settings['core_uri']
    js_build = request.registry.settings['js_build']
    js_log_level = request.registry.settings['js_log_level']

    return {'project': 'mist.io',
            'session': session,
            'email': email,
            'supported_providers': SUPPORTED_PROVIDERS,
            'core_uri': core_uri,
            'js_build': js_build,
            'js_log_level': js_log_level}


@view_config(route_name='backends', request_method='GET', renderer='json')
def list_backends(request):
    """Gets the available backends.

    .. note:: Currently, this is only used by the backends controller in js.
    """
    try:
        backends = request.environ['beaker.session']['backends']
    except:
        backends = request.registry.settings['backends']

    ret = []
    for backend_id in backends:
        backend = backends[backend_id]
        ret.append({'id': backend_id,
                    'title': backend['title'],
                    'provider': backend['provider'],
                    'poll_interval': backend['poll_interval'],
                    'state': 'wait',
                    # for Provider.RACKSPACE_FIRST_GEN
                    'region': backend.get('region', None),
                    # for Provider.RACKSPACE (the new Nova provider)
                    'datacenter': backend.get('datacenter', None),
                    'enabled': backend.get('enabled', True),
                     })

    return ret


@view_config(route_name='backends', request_method='POST', renderer='json')
def add_backend(request, renderer='json'):
    params = request.json_body
    provider = params.get('provider', '0')['provider']
    apikey = params.get('apikey', '')
    apisecret = params.get('apisecret', '')
    region = ''
    if not provider.__class__ is int and ':' in provider:
        region = provider.split(':')[1]
        provider = provider.split(':')[0]

    if not provider or not apikey or not apisecret:
        return Response('Invalid backend data', 400)

    backend_id = sha256('%s%s%s' % (provider, region, apikey)).hexdigest()

    backend = {'title': params.get('provider', '0')['title'],
               'provider': provider,
               'apikey': apikey,
               'apisecret': apisecret,
               'region': region,
               'poll_interval': request.registry.settings['default_poll_interval'],
               'enabled': 1,
              }

    request.registry.settings['backends'][backend_id] = backend
    save_settings(request)

    ret = {'id'           : backend_id,
           'apikey'       : backend['apikey'],
           'title'        : backend['title'],
           'provider'     : backend['provider'],
           'poll_interval': backend['poll_interval'],
           'region'       : backend['region'],
           'status'       : 'off',
           'enabled'      : 1,
          }
    return ret


@view_config(route_name='backend_action', request_method='DELETE', renderer='json')
def delete_backend(request, renderer='json'):
    request.registry.settings['backends'].pop(request.matchdict['backend'])
    save_settings(request)

    return Response('OK', 200)


@view_config(route_name='machines', request_method='GET', renderer='json')
def list_machines(request):
    """Gets machines and their metadata for a backend.

    Because each provider stores metadata in different places several checks
    are needed.

    The folowing are considered:::

        * For tags, Rackspace stores them in extra.metadata.tags while EC2 in
          extra.tags.tags.
        * For images, both EC2 and Rackpace have an image and an etra.imageId
          attribute
        * For flavors, EC2 has an extra.instancetype attribute while Rackspace
          an extra.flavorId. however we also expect to get size attribute.
    """
    try:
        conn = connect(request)
    except RuntimeError as e:
        log.error(e)
        return Response('Internal server error: %s' % e, 503)
    except:
        return Response('Backend not found', 404)

    try:
        machines = conn.list_nodes()
    except:
        return Response('Backend unavailable', 503)

    ret = []
    for m in machines:
        tags = m.extra.get('tags', None) or m.extra.get('metadata', None)
        tags = tags or {}
        tags = [value for key, value in tags.iteritems() if key != 'Name']

        if m.extra.get('availability', None):
            # for EC2
            tags.append(m.extra['availability'])
        elif m.extra.get('DATACENTERID', None):
            # for Linode
            tags.append(LINODE_DATACENTERS[m.extra['DATACENTERID']])

        image_id = m.image or m.extra.get('imageId', None)

        size = m.size or m.extra.get('flavorId', None)
        size = size or m.extra.get('instancetype', None)

        machine = {'id'            : m.id,
                   'uuid'          : m.get_uuid(),
                   'name'          : m.name,
                   'imageId'       : image_id,
                   'size'          : size,
                   'state'         : STATES[m.state],
                   'private_ips'   : m.private_ips,
                   'public_ips'    : m.public_ips,
                   'tags'          : tags,
                   'extra'         : m.extra,
                  }
        machine.update(get_machine_actions(m, conn))
        ret.append(machine)
    return ret


@view_config(route_name='machines', request_method='POST', renderer='json')
def create_machine(request):
    """Creates a new virtual machine on the specified backend.

    If the backend is Rackspace it attempts to deploy the node with an ssh key
    provided in config. the method used is the only one working in the old
    Rackspace backend. create_node(), from libcloud.compute.base, with 'auth'
    kwarg doesn't do the trick. Didn't test if you can upload some ssh related
    files using the 'ex_files' kwarg from openstack 1.0 driver.

    In Linode creation is a bit different. There you can pass the key file
    directly during creation. The Linode API also requires to set a disk size
    and doesn't get it from size.id. So, send size.disk from the client and
    use it in all cases just to avoid provider checking. Finally, Linode API
    does not support association between a machine and the image it came from.
    We could set this, at least for machines created through mist.io in
    ex_comment, lroot or lconfig. lroot seems more appropriate. However,
    liblcoud doesn't support linode.config.list at the moment, so no way to
    get them. Also, it will create inconsistencies for machines created
    through mist.io and those from the Linode interface.
    """

    try:
        conn = connect(request)
    except:
        return Response('Backend not found', 404)

    backend_id = request.matchdict['backend']

    try:
        key_name = request.json_body['key']
    except:
        key_name = None
    
    try:
        keypairs = request.environ['beaker.session']['keypairs']
    except:
        keypairs = request.registry.settings.get('keypairs', {})

    if key_name:
        keypair = get_keypair_by_name(keypairs, key_name)
    else:
        keypair = get_keypair(keypairs)      

    if keypair:
        private_key = keypair['private']
        public_key = keypair['public']
    else:
        private_key = public_key = None

    try:
        machine_name = request.json_body['name']
        location_id = request.json_body['location']
        image_id = request.json_body['image']
        size_id = request.json_body['size']
        #deploy_script received as unicode, but ScriptDeployment wants str
        script = str(request.json_body.get('script', ''))
        # these are required only for Linode, passing them anyway
        image_extra = request.json_body['image_extra']
        disk = request.json_body['disk']
    except Exception as e:
        return Response('Invalid payload', 400)

    size = NodeSize(size_id, name='', ram='', disk=disk, bandwidth='',
                    price='', driver=conn)
    image = NodeImage(image_id, name='', extra=image_extra, driver=conn)

    if conn.type in EC2_PROVIDERS:
        locations = conn.list_locations()
        for loc in locations:
            if loc.id == location_id:
                location = loc
                break
    else:
        location = NodeLocation(location_id, name='', country='', driver=conn)
    
    if conn.type in [Provider.RACKSPACE_FIRST_GEN, Provider.RACKSPACE] and\
    public_key:
        key = SSHKeyDeployment(str(public_key))
        deploy_script = ScriptDeployment(script)
        msd = MultiStepDeployment([key, deploy_script])        
        try:
            node = conn.deploy_node(name=machine_name,
                             image=image,
                             size=size,
                             location=location,
                             deploy=msd)
            if keypair:
                machines = keypair.get('machines', None)
                if machines and len(machines):
                    keypair['machines'].append([backend_id, node.id])
                else:
                    keypair['machines'] = [[backend_id, node.id],]
                save_keypairs(request, keypair)
        except Exception as e:
            return Response('Something went wrong with node creation in RackSpace: %s' % e, 500)
    elif conn.type in EC2_PROVIDERS and public_key:
        imported_key = import_key(conn, public_key, key_name)
        created_security_group = create_security_group(conn, EC2_SECURITYGROUP)
        deploy_script = ScriptDeployment(script)

        (tmp_key, tmp_key_path) = tempfile.mkstemp()
        key_fd = os.fdopen(tmp_key, 'w+b')
        key_fd.write(private_key)
        key_fd.close()
        #deploy_node wants path for ssh private key
        if imported_key and created_security_group:
            try:
                node = conn.deploy_node(name=machine_name,
                                 image=image,
                                 size=size,
                                 deploy=deploy_script,
                                 location=location,
                                 ssh_key=tmp_key_path,
                                 ex_keyname=key_name,
                                 ex_securitygroup=EC2_SECURITYGROUP['name'])

                if keypair:
                    machines = keypair.get('machines', None)
                    if machines and len(machines):
                        keypair['machines'].append([backend_id, node.id])
                    else:
                        keypair['machines'] = [[backend_id, node.id],]
                    save_keypairs(request, keypair)
            except Exception as e:
                return Response('Something went wrong with node creation in EC2: %s' % e, 500)
        #remove temp file with private key
        try:
            os.remove(tmp_key_path)
        except:
            pass
    elif conn.type is Provider.LINODE and public_key:
        auth = NodeAuthSSHKey(public_key)
        deploy_script = ScriptDeployment(script)
        try:
            node = conn.create_node(name=machine_name,
                             image=image,
                             size=size,
                             deploy=deploy_script,
                             location=location,
                             auth=auth)
            if keypair:
                machines = keypair.get('machines', None)
                if machines and len(machines):
                    keypair['machines'].append([backend_id, node.id])
                else:
                    keypair['machines'] = [[backend_id, node.id],]
                save_keypairs(request, keypair)
        except:
            return Response('Something went wrong with Linode creation', 500)

    else:
        try:
            node = conn.create_node(name=machine_name,
                             image=image,
                             size=size,
                             location=location)
        except Exception as e:
            return Response('Something went wrong with generic node creation: %s' % e, 500)

    return {'id': node.id,
            'name': node.name,
            'extra': node.extra,
            'public_ips': node.public_ips,
            'private_ips': node.private_ips,
            }



@view_config(route_name='machine', request_method='POST',
             request_param='action=start', renderer='json')
def start_machine(request):
    """Starts a machine on backends that support it.

    Currently only EC2 supports that.

    .. note:: Normally try won't get an AttributeError exception because this
              action is not allowed for machines that don't support it. Check
              helpers.get_machine_actions.
    """
    try:
        conn = connect(request)
    except:
        return Response('Backend not found', 404)

    machine_id = request.matchdict['machine']
    machine = Node(machine_id,
                   name=machine_id,
                   state=0,
                   public_ips=[],
                   private_ips=[],
                   driver=conn)
    try:
        # In liblcoud it is not possible to call this with machine.start()
        conn.ex_start_node(machine)
        Response('Success', 200)
    except AttributeError:
        return Response('Action not supported for this machine', 404)
    except:
        return []


@view_config(route_name='machine', request_method='POST',
             request_param='action=stop', renderer='json')
def stop_machine(request):
    """Stops a machine on backends that support it.

    Currently only EC2 supports that.

    .. note:: Normally try won't get an AttributeError exception because this
              action is not allowed for machines that don't support it. Check
              helpers.get_machine_actions.
    """
    try:
        conn = connect(request)
    except:
        return Response('Backend not found', 404)

    machine_id = request.matchdict['machine']
    machine = Node(machine_id,
                   name=machine_id,
                   state=0,
                   public_ips=[],
                   private_ips=[],
                   driver=conn)

    try:
        # In libcloud it is not possible to call this with machine.stop()
        conn.ex_stop_node(machine)
        Response('Success', 200)
    except AttributeError:
        return Response('Action not supported for this machine', 404)
    except:
        return []


@view_config(route_name='machine', request_method='POST',
             request_param='action=reboot', renderer='json')
def reboot_machine(request):
    """Reboots a machine on a certain backend."""
    try:
        conn = connect(request)
    except:
        return Response('Backend not found', 404)

    machine_id = request.matchdict['machine']
    machine = Node(machine_id,
                   name=machine_id,
                   state=0,
                   public_ips=[],
                   private_ips=[],
                   driver=conn)

    machine.reboot()

    return Response('Success', 200)


@view_config(route_name='machine', request_method='POST',
             request_param='action=destroy', renderer='json')
def destroy_machine(request):
    """Destroys a machine on a certain backend."""
    try:
        conn = connect(request)
    except:
        return Response('Backend not found', 404)

    machine_id = request.matchdict['machine']
    machine = Node(machine_id,
                   name=machine_id,
                   state=0,
                   public_ips=[],
                   private_ips=[],
                   driver=conn)

    machine.destroy()

    return Response('Success', 200)


@view_config(route_name='machine_metadata', request_method='POST',
             renderer='json')
def set_machine_metadata(request):
    """Sets metadata for a machine, given the backend and machine id.

    Libcloud handles this differently for each provider. Linode and Rackspace,
    at least the old Rackspace providers, don't support metadata adding.

    machine_id comes as u'...' but the rest are plain strings so use == when
    comparing in ifs. u'f' is 'f' returns false and 'in' is too broad.
    """
    try:
        conn = connect(request)
    except:
        return Response('Backend not found', 404)

    if conn.type in [Provider.LINODE, Provider.RACKSPACE_FIRST_GEN]:
        return Response('Adding metadata is not supported in this provider',
                        501)

    machine_id = request.matchdict['machine']

    try:
        tag = request.json_body['tag']
        unique_key = 'mist.io_tag-' + datetime.now().isoformat()
        pair = {unique_key: tag}
    except:
        return Response('Malformed metadata format', 400)

    if conn.type in EC2_PROVIDERS:
        try:
            machine = Node(machine_id,
                           name='',
                           state=0,
                           public_ips=[],
                           private_ips=[],
                           driver=conn)
            conn.ex_create_tags(machine, pair)
        except:
            return Response('Error while creating tag in EC2', 503)
    else:
        try:
            nodes = conn.list_nodes()
            for node in nodes:
                if node.id == machine_id:
                    machine = node
                    break
        except:
            return Response('Machine not found', 404)

        try:
            machine.extra['metadata'].update(pair)
            conn.ex_set_metadata(machine, pair)
        except:
            return Response('Error while creating tag', 503)

    return Response('Success', 200)


@view_config(route_name='machine_metadata', request_method='DELETE',
             renderer='json')
def delete_machine_metadata(request):
    """Delete metadata for a machine, given the machine id and the tag to be
    deleted.

    Libcloud handles this differently for each provider. Linode and Rackspace,
    at least the old Rackspace providers, don't support metadata updating. In
    EC2 you can delete just the tag you like. In Openstack you can only set a
    new list and not delete from the existing.

    Mist.io client knows only the value of the tag and not it's key so it
    has to loop through the machine list in order to find it.

    Don't forget to check string encoding before using them in ifs.
    u'f' is 'f' returns false.
    """
    try:
        conn = connect(request)
    except:
        return Response('Backend not found', 404)

    if conn.type in [Provider.LINODE, Provider.RACKSPACE_FIRST_GEN]:
        return Response('Updating metadata is not supported in this provider',
                        501)

    try:
        tag = request.json_body['tag']
    except:
        return Response('Malformed metadata format', 400)

    machine_id = request.matchdict['machine']

    try:
        nodes = conn.list_nodes()
        for node in nodes:
            if node.id == machine_id:
                machine = node
                break
    except:
        return Response('Machine not found', 404)

    if conn.type in EC2_PROVIDERS:
        tags = machine.extra.get('tags', None)
        try:
            for mkey, mdata in tags.iteritems():
                if tag == mdata:
                    pair = {mkey: tag}
                    break
        except:
            return Response('Tag not found', 404)

        try:
            conn.ex_delete_tags(machine, pair)
        except:
            return Response('Error while deleting metadata in EC2', 503)
    else:
        tags = machine.extra.get('metadata', None)
        try:
            for mkey, mdata in tags.iteritems():
                if tag == mdata:
                    tags.pop(mkey)
                    break
        except:
            return Response('Tag not found', 404)

        try:
            conn.ex_set_metadata(machine, tags)
        except:
            return Response('Error while updating metadata', 503)

    return Response('Success', 200)


@view_config(route_name='machine_shell', request_method='POST',
             renderer='json')
def shell_command(request):
    """Send a shell command to a machine over ssh, using fabric."""
    try:
        conn = connect(request)
    except:
        return Response('Backend not found', 404)

    machine_id = request.matchdict['machine']
    backend_id = request.matchdict['backend']
    host = request.params.get('host', None)
    ssh_user = request.params.get('ssh_user', None)
    command = request.params.get('command', None)

    if not ssh_user or ssh_user == 'undefined':
        ssh_user = 'root'

    try:
        keypairs = request.environ['beaker.session']['keypairs']
    except:
        keypairs = request.registry.settings.get('keypairs', {})

    keypair = get_keypair(keypairs, backend_id, machine_id)
    
    if keypair:
        private_key = keypair['private']
        public_key = keypair['public']
    else:
        private_key = public_key = None

    return run_command(conn, machine_id, host, ssh_user, private_key, command)


@view_config(route_name='images', request_method='GET', renderer='json')
def list_images(request):
    """List images from each backend."""
    try:
        conn = connect(request)
    except:
        return Response('Backend not found', 404)

    try:
        if conn.type in EC2_PROVIDERS:
            images = conn.list_images(None, EC2_IMAGES[conn.type].keys())
            for image in images:
                image.name = EC2_IMAGES[conn.type][image.id]
        else:
            images = conn.list_images()
    except:
        return Response('Backend unavailable', 503)

    ret = []
    for image in images:
        ret.append({'id'    : image.id,
                    'extra' : image.extra,
                    'name'  : image.name,
                    })
    return ret


@view_config(route_name='sizes', request_method='GET', renderer='json')
def list_sizes(request):
    """List sizes (aka flavors) from each backend."""
    try:
        conn = connect(request)
    except:
        return Response('Backend not found', 404)

    try:
        sizes = conn.list_sizes()
    except:
        return Response('Backend unavailable', 503)

    ret = []
    for size in sizes:
        ret.append({'id'        : size.id,
                    'bandwidth' : size.bandwidth,
                    'disk'      : size.disk,
                    'driver'    : size.driver.name,
                    'name'      : size.name,
                    'price'     : size.price,
                    'ram'       : size.ram,
                    })

    return ret


@view_config(route_name='locations', request_method='GET', renderer='json')
def list_locations(request):
    """List locations from each backend.

    Locations mean different things in each backend. e.g. EC2 uses it as a
    datacenter in a given availability zone, whereas Linode lists availability
    zones. However all responses share id, name and country eventhough in some
    cases might be empty, e.g. Openstack.

    In EC2 all locations by a provider have the same name, so the availability
    zones are listed instead of name.
    """
    try:
        conn = connect(request)
    except:
        return Response('Backend not found', 404)

    try:
        locations = conn.list_locations()
    except:
        return Response('Backend unavailable', 503)

    ret = []
    for location in locations:
        if conn.type in EC2_PROVIDERS:
            name = location.availability_zone.name
        else:
            name = location.name

        ret.append({'id'        : location.id,
                    'name'      : name,
                    'country'   : location.country,
                    })

    return ret


@view_config(route_name='keys', request_method='GET', renderer='json')
def list_keys(request):
    """List keys.

    List all key pairs that are configured on this server. Only the public
    keys are returned.

    """
    try:
        keypairs = request.environ['beaker.session']['keypairs']
    except:
        keypairs = request.registry.settings.get('keypairs', {})

    ret = [{'name': key, 
            'machines': keypairs[key].get('machines', False),
            'pub': keypairs[key]['public'],
            'default_key': keypairs[key].get('default', False)} 
           for key in keypairs.keys()]
    return ret


@view_config(route_name='keys', request_method='POST', renderer='json')
def generate_keypair(request):
    """Generate a random keypair"""
    key = RSA.generate(2048, os.urandom)
    return {'public': key.exportKey('OpenSSH'),
            'private': key.exportKey()}


@view_config(route_name='key', request_method='PUT', renderer='json')
def add_key(request):
    params = request.json_body
    id = params.get('name', '')

    key = {'public' : params.get('pub', ''),
           'private' : params.get('priv', '')}

    if not len(request.registry.settings['keypairs']):
        key['default'] = True
  
    request.registry.settings['keypairs'][id] = key
    save_settings(request)

    ret = {'name': id, 
           'pub': key['public'], 
           'priv': key['private'], 
           'default_key': key.get('default', False),
           'machines': []}

    return ret


@view_config(route_name='key', request_method='POST', renderer='json')
def set_default_key(request):
    params = request.json_body
    id = params.get('name', '')

    keypairs = request.registry.settings['keypairs']
    
    for key in keypairs:
        if keypairs[key].get('default', False):
            keypairs[key]['default'] = False
 
    keypairs[id]['default'] = True
  
    save_settings(request)

    return {}


@view_config(route_name='key_machines_associate', request_method='POST', renderer='json')
def associate_key_to_machines(request):
    '''Associate a key with list of machines. 
       Receives a key name, and a list of machine/backend ids'''
    params = request.json_body
    key_name = params.get('key_name', '')
    machine_backend_list = params.get('machine_backend_list', '')

    try:
        keypairs = request.environ['beaker.session']['keypairs']
    except:
        keypairs = request.registry.settings.get('keypairs', {})
    
    keypair = {}

    if key_name in keypairs.keys():
        keypair = keypairs[key_name]
    else:
        return Response('Keypair not found', 404)



    #machine_backend_list = [[machine1_id, backend1_id], [machine2_id, backend2_id], [machine3_id, backend3_id]]
    if keypair:
        keypair['machines'] = []
        for pair in machine_backend_list:
            try:
                machine_id = pair[0]
                backend_id = pair[1]
            except:
                continue

	    keypair['machines'].append(pair)


    save_keypairs(request, keypair)

    return {}


@view_config(route_name='key_machine_associate', request_method='POST', renderer='json')
def associate_key_to_machine(request):
    '''Associate a key with a machine. 
       Receives a key name, and a machine/backend id'''
    params = request.json_body
    key_name = params.get('key_name', '')
    machine_id = params.get('machine_id', '')
    backend_id = params.get('backend_id', '')

    try:
        keypairs = request.environ['beaker.session']['keypairs']
    except:
        keypairs = request.registry.settings.get('keypairs', {})
    
    keypair = {}

    if key_name in keypairs.keys():
        keypair = keypairs[key_name]
    else:
        return Response('Keypair not found', 404)

    machine_backend = [backend_id, machine_id]

    if not machine_backend in keypair['machines']:
        keypair['machines'].append(machine_backend)

    save_keypairs(request, keypair)

    return {}

@view_config(route_name='key_disassociate', request_method='POST', renderer='json')
def disassociate_key_to_machine(request):
    '''Disassociate a key from a machine. 
       Receives a key name, and a machine/backend id pair and removes the machine from that keypair'''
    params = request.json_body
    key_name = params.get('key_name', '')
    machine_id = params.get('machine_id', '')
    backend_id = params.get('backend_id', '')

    try:
        keypairs = request.environ['beaker.session']['keypairs']
    except:
        keypairs = request.registry.settings.get('keypairs', {})
    
    keypair = {}

    if key_name in keypairs.keys():
        keypair = keypairs[key_name]
    else:
        return Response('Keypair not found', 404)

    machine_backend = [backend_id, machine_id]

    for pair in keypair['machines']:
        if pair == machine_backend:
            keypair['machines'].remove(pair)
            save_keypairs(request, keypair)
            break

    return {}


@view_config(route_name='key_private', request_method='POST', renderer='json')
def get_private_key(request):
    """get private key from keypair name, for display
       on key view when user clicks display private key 
    """
    params = request.json_body
    key_name = params.get('key_name', '')

    try:
        keypairs = request.environ['beaker.session']['keypairs']
    except:
        keypairs = request.registry.settings.get('keypairs', {})
    
    keypair = {}

    if key_name in keypairs.keys():
        keypair = keypairs[key_name]
    else:
        return Response('Keypair not found', 404)


    if keypair:
        return keypair.get('private', '')

@view_config(route_name='key', request_method='DELETE', renderer='json')
def delete_key(request):
    params = request.json_body
    id = params.get('name', '')

    key = request.registry.settings['keypairs'].pop(id)
    if key.get('default', None):
        #if we delete the default key, make the next one as default, provided 
        #that it exists
        try:
           first_key_id = request.registry.settings['keypairs'].keys()[0]
           request.registry.settings['keypairs'][first_key_id]['default'] = True
        except KeyError: 
            pass
    save_settings(request)

    return {}


@view_config(route_name='monitoring', request_method='GET', renderer='json')
def check_monitoring(request):
    """
    Ask the mist.io service if monitoring is enabled for this machine
    """
    core_uri = request.registry.settings['core_uri']
    email = request.registry.settings.get('email','')
    password = request.registry.settings.get('password','')
    
    timestamp = datetime.utcnow().strftime("%s")
    hash = sha256("%s:%s:%s" % (email, timestamp, password)).hexdigest()
    
    payload = {'email': email,
               'timestamp': timestamp,
               'hash': hash,
               }
    
    ret = requests.get(core_uri+request.path, params=payload, verify=False)
    if ret.status_code == 200:
        return ret.json()
    else:
        return Response('Service unavailable', 503)


@view_config(route_name='update_monitoring', request_method='POST', renderer='json')
def update_monitoring(request):
    """
    Enable/disable monitoring for this machine using the hosted mist.io service.
    """
    core_uri = request.registry.settings['core_uri']
    try:
        email = request.json_body['email']
        password = request.json_body['pass']
        timestamp = request.json_body['timestamp']
        hash = request.json_body['hash']
    except:
        email = request.registry.settings.get('email','')
        password = request.registry.settings.get('password','')
        timestamp =  datetime.utcnow().strftime("%s")
        hash = sha256("%s:%s:%s" % (email, timestamp, password)).hexdigest()
        
    action = request.json_body['action'] or 'enable'
    payload = {'email': email,
               'timestamp': timestamp,
               'hash': hash,
               'action': action,
               }
    #TODO: make ssl verification configurable globally, set to true by default    
    ret = requests.post(core_uri+request.path, params=payload, verify=False)
    
    if ret.status_code != 200:
        return Response('Service unavailable', 503)

    request.registry.settings['email'] = email
    request.registry.settings['password'] = password
    save_settings(request)
    return ret.json()
