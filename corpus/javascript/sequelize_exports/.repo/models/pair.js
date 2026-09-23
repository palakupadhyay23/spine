const { DataTypes } = require('sequelize');
const sequelize = require('./db');

sequelize.define('tune', { bars: DataTypes.INTEGER });
sequelize.define('Tune', { bars: DataTypes.INTEGER });

mixin(module.exports);
