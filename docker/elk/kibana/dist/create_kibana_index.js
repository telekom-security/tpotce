'use strict';

var _setup_error = require('./setup_error');

var _setup_error2 = _interopRequireDefault(_setup_error);

function _interopRequireDefault(obj) { return obj && obj.__esModule ? obj : { default: obj }; }

module.exports = function (server, mappings) {
  var _server$plugins$elast = server.plugins.elasticsearch.getCluster('admin');

  const callWithInternalUser = _server$plugins$elast.callWithInternalUser;

  const index = server.config().get('kibana.index');

  function handleError(message) {
    return function (err) {
      throw new _setup_error2.default(server, message, err);
    };
  }

  return callWithInternalUser('indices.create', {
    index: index,
    body: {
      settings: {
        number_of_shards: 1,
        number_of_replicas: 0,
        'index.mapper.dynamic': false
      },
      mappings
    }
  }).catch(handleError('Unable to create Kibana index "<%= kibana.index %>"')).then(function () {
    return callWithInternalUser('cluster.health', {
      waitForStatus: 'yellow',
      index: index
    }).catch(handleError('Waiting for Kibana index "<%= kibana.index %>" to come online failed.'));
  });
};
