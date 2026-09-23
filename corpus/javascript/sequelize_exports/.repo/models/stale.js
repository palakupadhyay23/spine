const { DataTypes } = require('sequelize');
const sequelize = require('./db');

const Sonata = sequelize.define('concerto', { movements: DataTypes.INTEGER });

module.exports = make();
exports.Etude = sequelize.define('etude', { bars: DataTypes.INTEGER });
