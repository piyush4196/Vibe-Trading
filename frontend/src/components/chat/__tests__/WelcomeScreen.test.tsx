import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WelcomeScreen } from "../WelcomeScreen";

describe("WelcomeScreen", () => {
  const onExample = vi.fn();

  beforeEach(() => onExample.mockClear());

  it("renders the title", () => {
    render(<WelcomeScreen onExample={onExample} />);
    expect(screen.getByText("Vibe-Trading")).toBeInTheDocument();
  });

  it("renders capability chips", () => {
    render(<WelcomeScreen onExample={onExample} />);
    expect(screen.getByText("Finance Skills Library")).toBeInTheDocument();
    expect(screen.getByText("Swarm Agent Teams")).toBeInTheDocument();
    expect(screen.getByText("Shadow Account Backtest")).toBeInTheDocument();
    expect(screen.getByText("NSE · BSE · F&O · Nifty")).toBeInTheDocument();
  });

  it("renders example categories", () => {
    render(<WelcomeScreen onExample={onExample} />);
    expect(screen.getByText("India Equity Backtest")).toBeInTheDocument();
    expect(screen.getByText("India Live Desk & F&O")).toBeInTheDocument();
    expect(screen.getByText("Research & Analysis")).toBeInTheDocument();
    expect(screen.getByText("Swarm Teams")).toBeInTheDocument();
  });

  it("calls onExample with prompt when an example button is clicked", async () => {
    render(<WelcomeScreen onExample={onExample} />);
    const user = userEvent.setup();
    await user.click(screen.getByText("Nifty Blue-Chip Portfolio"));
    expect(onExample).toHaveBeenCalledTimes(1);
    expect(onExample).toHaveBeenCalledWith(
      expect.stringContaining("RELIANCE.NS"),
    );
  });

  it("renders Indian live-desk examples", () => {
    render(<WelcomeScreen onExample={onExample} />);
    expect(screen.getByText("Paper Buy 1 Qty Reliance")).toBeInTheDocument();
    expect(screen.getByText("Nifty ATM Options Entry/Exit")).toBeInTheDocument();
    expect(screen.getByText("Bank Nifty Intraday Setup")).toBeInTheDocument();
    expect(screen.getByText("Bank Nifty ATM Options")).toBeInTheDocument();
    expect(screen.getByText("HDFC Bank Stock Options")).toBeInTheDocument();
  });

  it("renders the helper text", () => {
    render(<WelcomeScreen onExample={onExample} />);
    expect(
      screen.getByText(/Describe an India trading idea to get started/),
    ).toBeInTheDocument();
    expect(screen.getByText("Try an example:")).toBeInTheDocument();
  });
});
