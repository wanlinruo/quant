import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Meta } from "../api/types";
import { currentLocation, mkMeta, renderAt } from "../test/utils";
import { useAccount } from "./useAccount";

function Probe({ meta }: { meta: Meta }) {
  const { account, setAccount } = useAccount(meta);
  return (
    <>
      <span data-testid="account">{account}</span>
      <button onClick={() => setAccount("live")}>to-live</button>
    </>
  );
}

describe("useAccount", () => {
  it("uses default account when no param", () => {
    renderAt("/portfolio", <Probe meta={mkMeta()} />);
    expect(screen.getByTestId("account")).toHaveTextContent("paper");
  });

  it("falls back on unknown account", () => {
    renderAt("/portfolio?account=foo", <Probe meta={mkMeta()} />);
    expect(screen.getByTestId("account")).toHaveTextContent("paper");
  });

  it("falls back when account has no data", () => {
    renderAt("/portfolio?account=live", <Probe meta={mkMeta()} />);
    expect(screen.getByTestId("account")).toHaveTextContent("paper");
  });

  it("reads a valid account from the url", () => {
    renderAt("/portfolio?account=live", <Probe meta={mkMeta({ has: { live: true } })} />);
    expect(screen.getByTestId("account")).toHaveTextContent("live");
  });

  it("keeps other params when switching", async () => {
    const { user } = renderAt("/backtest?run=3", <Probe meta={mkMeta({ has: { live: true } })} />);
    await user.click(screen.getByText("to-live"));
    expect(currentLocation()).toBe("/backtest?run=3&account=live");
    expect(screen.getByTestId("account")).toHaveTextContent("live");
  });
});
