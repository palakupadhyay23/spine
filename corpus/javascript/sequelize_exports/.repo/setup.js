const { Performer } = require('./models/player');
const { Stanza, Couplet } = require('./models/chain');
const { Lyric } = require('./models/nested');
const { Aria } = require('./models/solo');
const Duet = require('./models/duet');
const { Rondo } = require('./models/mixed');
const { Etude, Sonata } = require('./models/stale');
const { TUNE } = require('./models/pair');

function wire(sequelize) {
  const { orchestra } = sequelize.models;
  Performer.belongsTo(orchestra);
  Stanza.belongsTo(orchestra);
  Lyric.belongsTo(orchestra);
  Aria.belongsTo(orchestra);
  Couplet.belongsTo(Stanza);
  Duet.belongsTo(orchestra);
  Rondo.belongsTo(orchestra);
  Etude.belongsTo(orchestra);
  Sonata.belongsTo(orchestra);
  TUNE.belongsTo(orchestra);
}

module.exports = { wire };
