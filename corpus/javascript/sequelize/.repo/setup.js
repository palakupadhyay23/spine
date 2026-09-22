const { Musician } = require('./models/musician');

function applyExtraSetup(sequelize) {
  const { instrument, orchestra, ghost } = sequelize.models;

  orchestra.hasMany(instrument);
  instrument.belongsTo(orchestra);
  Musician.belongsTo(orchestra);
  orchestra.belongsToMany(instrument, { through: 'lineup' });
  ghost.belongsTo(orchestra);
}

module.exports = { applyExtraSetup };
