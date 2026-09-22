const { Musician } = require('./models/musician');

function applyExtraSetup(sequelize) {
  const { instrument, orchestra, venue, ghost } = sequelize.models;

  orchestra.hasMany(instrument);
  instrument.belongsTo(orchestra);
  orchestra.hasMany(venue);
  Musician.belongsTo(orchestra);
  Musician.belongsToMany(venue, { through: 'booking' });
  ghost.belongsTo(orchestra);
}

module.exports = { applyExtraSetup };
