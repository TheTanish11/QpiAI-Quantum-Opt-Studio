const { expect } = require("chai");
const { ethers } = require("hardhat");

const id = (s) => ethers.id(s);
const S = { REGISTERED: 1, IN_SERVICE: 2, RETIRED: 3, COLLECTED: 4, UNDER_ASSESSMENT: 5, SECOND_LIFE: 6,
            RECYCLING_APPROVED: 7, IN_PROCESSING: 8, MATERIALS_RECORDED: 9, RECONCILED: 10 };

describe("Circulytics", () => {
  let c, admin, maker, collector, recycler, auditor, attester, stranger, buyer;
  beforeEach(async () => {
    [admin, maker, collector, recycler, auditor, attester, stranger, buyer] = await ethers.getSigners();
    c = await (await ethers.getContractFactory("Circulytics")).deploy(admin.address);
    await c.grantRole(await c.MANUFACTURER(), maker.address);
    await c.grantRole(await c.COLLECTOR(), collector.address);
    await c.grantRole(await c.RECYCLER(), recycler.address);
    await c.grantRole(await c.AUDITOR(), auditor.address);
    await c.grantRole(await c.ATTESTER(), attester.address);
    await c.grantRole(await c.COLLECTOR(), buyer.address); // a participant who can claim
  });

  const bid = id("BAT-1");
  async function toProcessing() {
    await c.connect(maker).registerBattery(bid, id("root"));
    await c.connect(maker).advanceState(bid, S.IN_SERVICE, ethers.ZeroHash);
    await c.connect(maker).advanceState(bid, S.RETIRED, ethers.ZeroHash);
    await c.connect(maker).proposeTransfer(bid, collector.address);
    await c.connect(collector).acceptTransfer(bid);
    await c.connect(collector).advanceState(bid, S.COLLECTED, ethers.ZeroHash);
    await c.connect(collector).anchorEvidence(id("health-report"));
    await c.connect(collector).advanceState(bid, S.UNDER_ASSESSMENT, id("health-report"));
    await c.connect(collector).proposeTransfer(bid, recycler.address);
    await c.connect(recycler).acceptTransfer(bid);
    await c.connect(recycler).advanceState(bid, S.RECYCLING_APPROVED, id("health-report"));
    await c.connect(attester).anchorEvidence(id("weigh-ticket"));
    await c.connect(recycler).advanceState(bid, S.IN_PROCESSING, id("weigh-ticket"));
  }

  it("registers a battery once; rejects duplicates and non-manufacturers", async () => {
    await expect(c.connect(maker).registerBattery(bid, id("r"))).to.emit(c, "BatteryRegistered");
    await expect(c.connect(maker).registerBattery(bid, id("r"))).to.be.revertedWith("already registered");
    await expect(c.connect(recycler).registerBattery(id("X"), id("r"))).to.be.reverted;
  });

  it("walks the full lifecycle with explicit custody acceptance", async () => {
    await toProcessing();
    expect((await c.batteries(bid)).state).to.equal(S.IN_PROCESSING);
    expect((await c.batteries(bid)).custodian).to.equal(recycler.address);
  });

  it("rejects skipped states", async () => {
    await c.connect(maker).registerBattery(bid, id("r"));
    await expect(c.connect(maker).advanceState(bid, S.RECYCLING_APPROVED, ethers.ZeroHash)).to.be.revertedWith("invalid transition");
  });

  it("requires anchored evidence for assessment/processing", async () => {
    await c.connect(maker).registerBattery(bid, id("r"));
    await c.connect(maker).advanceState(bid, S.IN_SERVICE, ethers.ZeroHash);
    await c.connect(maker).advanceState(bid, S.RETIRED, ethers.ZeroHash);
    await c.connect(maker).proposeTransfer(bid, collector.address);
    await c.connect(collector).acceptTransfer(bid);
    await c.connect(collector).advanceState(bid, S.COLLECTED, ethers.ZeroHash);
    await expect(c.connect(collector).advanceState(bid, S.UNDER_ASSESSMENT, id("nope"))).to.be.revertedWith("evidence not anchored");
  });

  it("only the custodian may transition or propose a transfer", async () => {
    await c.connect(maker).registerBattery(bid, id("r"));
    await expect(c.connect(collector).advanceState(bid, S.IN_SERVICE, ethers.ZeroHash)).to.be.reverted;
    await expect(c.connect(collector).proposeTransfer(bid, recycler.address)).to.be.revertedWith("not custodian");
  });

  it("transfer is pending until accepted and only by the proposed receiver", async () => {
    await c.connect(maker).registerBattery(bid, id("r"));
    await c.connect(maker).proposeTransfer(bid, collector.address);
    expect((await c.batteries(bid)).custodian).to.equal(maker.address);
    await expect(c.connect(recycler).acceptTransfer(bid)).to.be.revertedWith("not the proposed receiver");
    await expect(c.connect(maker).proposeTransfer(bid, recycler.address)).to.be.revertedWith("transfer pending");
  });

  it("cannot propose a transfer to an unauthorised wallet", async () => {
    await c.connect(maker).registerBattery(bid, id("r"));
    await expect(c.connect(maker).proposeTransfer(bid, stranger.address)).to.be.revertedWith("receiver not authorised");
  });

  it("revocation blocks subsequent actions and pending acceptance", async () => {
    await c.connect(maker).registerBattery(bid, id("r"));
    await c.connect(maker).proposeTransfer(bid, collector.address);
    await c.revokeRole(await c.COLLECTOR(), collector.address);
    await expect(c.connect(collector).acceptTransfer(bid)).to.be.revertedWith("authorisation revoked");
    await c.revokeRole(await c.MANUFACTURER(), maker.address);
    await expect(c.connect(maker).registerBattery(id("Y"), id("r"))).to.be.reverted;
  });

  it("evidence hash can be anchored once", async () => {
    await c.connect(collector).anchorEvidence(id("e1"));
    await expect(c.connect(collector).anchorEvidence(id("e1"))).to.be.revertedWith("already anchored");
    await expect(c.connect(stranger).anchorEvidence(id("e2"))).to.be.revertedWith("not authorised");
  });

  describe("claims", () => {
    const oid = id("OUT-CU");
    beforeEach(async () => { await c.connect(recycler).recordOutput(oid, 50_000); }); // 50 kg in grams
    it("accepts valid claims and enforces the cap", async () => {
      await c.connect(buyer).createClaim(id("C1"), oid, 35_000);
      await expect(c.connect(collector).createClaim(id("C2"), oid, 35_000)).to.be.revertedWith("exceeds unclaimed balance");
      expect(await c.unclaimed(oid)).to.equal(15_000);
    });
    it("rejects duplicate claim ids", async () => {
      await c.connect(buyer).createClaim(id("C1"), oid, 10_000);
      await expect(c.connect(collector).createClaim(id("C1"), oid, 10_000)).to.be.revertedWith("duplicate claim id");
    });
    it("rejects claims on unknown outputs and from strangers", async () => {
      await expect(c.connect(buyer).createClaim(id("C9"), id("none"), 1)).to.be.revertedWith("unknown output");
      await expect(c.connect(stranger).createClaim(id("C8"), oid, 1)).to.be.revertedWith("not authorised");
    });
    it("release restores balance, only by claimant, only once", async () => {
      await c.connect(buyer).createClaim(id("C1"), oid, 50_000);
      await expect(c.connect(collector).releaseClaim(id("C1"))).to.be.revertedWith("not claimant");
      await c.connect(buyer).releaseClaim(id("C1"));
      await expect(c.connect(buyer).releaseClaim(id("C1"))).to.be.revertedWith("not active");
      expect(await c.unclaimed(oid)).to.equal(50_000);
    });
    it("only a recycler may record outputs, once per id", async () => {
      await expect(c.connect(buyer).recordOutput(id("O2"), 5)).to.be.reverted;
      await expect(c.connect(recycler).recordOutput(oid, 5)).to.be.revertedWith("output exists");
    });
  });

  it("anchors an optimisation run record once, by admin/auditor only", async () => {
    await expect(c.anchorRun(id("rec"), id("in"), "classical-exact")).to.emit(c, "RunAnchored");
    await expect(c.anchorRun(id("rec"), id("in"), "x")).to.be.revertedWith("already anchored");
    await expect(c.connect(stranger).anchorRun(id("r2"), id("in"), "x")).to.be.revertedWith("not authorised");
    await expect(c.connect(auditor).anchorRun(id("r3"), id("in"), "x")).to.emit(c, "RunAnchored");
  });
});
