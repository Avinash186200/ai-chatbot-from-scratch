/** Tests run in Node with fetch mocked; nothing touches the network. */
module.exports = {
  preset: "ts-jest",
  testEnvironment: "node",
  roots: ["<rootDir>/test"],
  setupFiles: ["<rootDir>/test/setup.ts"],
  collectCoverageFrom: ["src/**/*.ts"],
};
