require("@nomicfoundation/hardhat-ethers");
require("@nomicfoundation/hardhat-chai-matchers");
const { subtask } = require("hardhat/config");
const { TASK_COMPILE_SOLIDITY_GET_SOLC_BUILD } = require("hardhat/builtin-tasks/task-names");

// On networks that block binaries.soliditylang.org run with SOLCJS=1 to use the npm `solc` package.
if (process.env.SOLCJS === "1") {
  subtask(TASK_COMPILE_SOLIDITY_GET_SOLC_BUILD, async (args, hre, runSuper) => {
    if (args.solcVersion === "0.8.24") {
      const compilerPath = require.resolve("solc/soljson.js");
      return { compilerPath, isSolcJs: true, version: "0.8.24", longVersion: require("solc").version() };
    }
    return runSuper();
  });
}

module.exports = { solidity: { version: "0.8.24", settings: { optimizer: { enabled: true, runs: 200 } } } };
