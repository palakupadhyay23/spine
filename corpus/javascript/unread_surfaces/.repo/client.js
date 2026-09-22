const frozen = require('./frozen');
const made = require('./made');
const later = require('./later');
const keyed = require('./keyed');
const grown = require('./grown');
const store = require('./store');
const { Handler } = require('./renamed');

function go(ids) {
  frozen.find();
  frozen.create();
  made.find();
  keyed.find();
  keyed.create();
  grown.find();
  grown.create();
  new Handler().run();
  ids.forEach(function (id) {
    var store = id;
    store.save();
  });
  try {
    later.find();
  } catch (store) {
    store.save();
  }
  return store.find();
}
