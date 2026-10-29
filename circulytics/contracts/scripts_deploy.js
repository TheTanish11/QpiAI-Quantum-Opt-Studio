// npx hardhat node  (terminal 1);  npx hardhat run scripts_deploy.js --network localhost
// Deploys Circulytics and grants the relayer (Hardhat account #1, a PUBLIC test key) ATTESTER + AUDITOR.
const { ethers } = require("hardhat");
(async () => {
  const [admin, relayer] = await ethers.getSigners();
  const c = await (await ethers.getContractFactory("Circulytics")).deploy(admin.address);
  await c.waitForDeployment();
  await (await c.grantRole(await c.ATTESTER(), relayer.address)).wait();
  await (await c.grantRole(await c.AUDITOR(), relayer.address)).wait();
  console.log(await c.getAddress());
})();
