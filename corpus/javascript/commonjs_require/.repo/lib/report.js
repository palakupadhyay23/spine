const format = require('./format');
const { money } = require('./format');
const pad = require('./format').pad;
const { money: cash } = require('./format');
const fs = require('fs');

function line(value) {
  return format.pad(value) + money(value) + pad(value);
}

function alias(value) {
  return cash(value);
}

function save(text) {
  return fs.writeFileSync('out.txt', text);
}
