const { Model, DataTypes } = require('sequelize');

class Musician extends Model {}

Musician.init({ name: DataTypes.STRING, age: DataTypes.INTEGER }, { sequelize: null });

module.exports = { Musician };
