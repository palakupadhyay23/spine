function real() {
  return 1;
}

function helper() {
  return 2;
}

const api = module.exports = { real };

{
  const api = {};
  api.run = helper;
}
