from __future__ import print_function

import json
import logging

from autobahn.twisted.websocket import (WebSocketClientFactory,
                                        WebSocketClientProtocol)
from twisted.internet.defer import Deferred
from twisted.internet.protocol import ReconnectingClientFactory

from . import Message, ServiceRequest
from .event_emitter import EventEmitterMixin

LOGGER = logging.getLogger('roslibpy')

class RosBridgeProtocol(WebSocketClientProtocol):
    """Implements the websocket client protocol to encode/decode JSON ROS Brige messages."""

    def __init__(self, *args, **kwargs):
        super(RosBridgeProtocol, self).__init__(*args, **kwargs)
        self.factory = None
        self._pending_service_requests = {}
        self._message_handlers = {
            'publish': self._handle_publish,
            'service_response': self._handle_service_response
        }
        # TODO: add handlers for op: call_service, status

    def send_ros_message(self, message):
        """Encode and serialize ROS Brige protocol message.

        Args:
            message (:class:`.Message`): ROS Brige Message to send.
        """
        try:
            self.sendMessage(json.dumps(dict(message)).encode('utf8'))
        except StandardError as exception:
            # TODO: Check if it makes sense to raise exception again here
            # Since this is wrapped in many layers of indirection
            LOGGER.exception('Failed to send message, %s', exception)

    def register_message_handlers(self, operation, handler):
        """Register a message handler for a specific operation type.

        Args:
            operation (:obj:`str`): ROS Bridge operation.
            handler: Callback to handle the message.
        """
        if operation in self._message_handlers:
            raise StandardError('Only one handler can be registered per operation')

        self._message_handlers[operation] = handler

    def send_ros_service_request(self, service_request, callback, errback):
        """Initiate a ROS service request through the ROS Bridge.

        Args:
            service_request (:class:`.ServiceRequest`): Service request.
            callback: Callback invoked on successful execution.
            errback: Callback invoked on error.
        """
        request_id = service_request['id']
        self._pending_service_requests[request_id] = (callback, errback)

        self.sendMessage(json.dumps(dict(service_request)).encode('utf8'))

    def onConnect(self, response):
        LOGGER.debug('Server connected: %s', response.peer)

    def onOpen(self):
        LOGGER.debug('Connection to ROS MASTER ready.')
        self.factory.ready(self)

    def onMessage(self, payload, isBinary):
        if isBinary:
            raise NotImplementedError('Add support for binary messages')

        message = Message(json.loads(payload.decode('utf8')))
        handler = self._message_handlers.get(message['op'], None)
        if not handler:
            raise StandardError('No handler registered for operation "%s"' % message['op'])

        handler(message)

    def _handle_publish(self, message):
        self.factory.emit(message['topic'], message['msg'])

    def _handle_service_response(self, message):
        request_id = message['id']
        service_handlers = self._pending_service_requests.get(request_id, None)

        if not service_handlers:
            raise StandardError('No handler registered for service request ID: "%s"' % request_id)

        callback, errback = service_handlers
        del self._pending_service_requests[request_id]

        if 'result' in message and message['result'] == False:
            if errback:
                errback(message['values'])
        else:
            if callback:
                callback(ServiceRequest(message['values']))

    def onClose(self, wasClean, code, reason):
        LOGGER.info('WebSocket connection closed: %s', reason)


class RosBridgeClientFactory(EventEmitterMixin, ReconnectingClientFactory, WebSocketClientFactory):
    """Factory to construct instance of the ROS Bridge protocol."""
    protocol = RosBridgeProtocol

    def __init__(self, *args, **kwargs):
        super(RosBridgeClientFactory, self).__init__(*args, **kwargs)
        self._on_ready_event = Deferred()

    def on_ready(self, callback):
        self._on_ready_event.addCallback(callback)

    def ready(self, proto):
        self._on_ready_event.callback(proto)

    def startedConnecting(self, connector):
        LOGGER.debug('Started to connect...')

    def clientConnectionLost(self, connector, reason):
        LOGGER.debug('Lost connection. Reason: %s', reason)
        ReconnectingClientFactory.clientConnectionLost(self, connector, reason)

    def clientConnectionFailed(self, connector, reason):
        LOGGER.debug('Connection failed. Reason: %s', reason)
        ReconnectingClientFactory.clientConnectionFailed(
            self, connector, reason)
        self._on_ready_event.errback(reason)
