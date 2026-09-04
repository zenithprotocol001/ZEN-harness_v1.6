import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DeveloperModeMenu } from "../components/DeveloperModeMenu";

describe("<DeveloperModeMenu /> (v1.5.1)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders nothing when open is false", () => {
    const { container } = render(
      <DeveloperModeMenu
        open={false}
        onClose={() => {}}
        developerMode={false}
        onToggleDeveloperMode={() => {}}
        llmOk={true}
        onOpenKeys={() => {}}
        onOpenConfig={() => {}}
      />,
    );
    expect(container.querySelector('[data-testid="chat-gear-menu"]')).toBeNull();
  });

  it("renders the menu items and LLM status when open", () => {
    render(
      <DeveloperModeMenu
        open={true}
        onClose={() => {}}
        developerMode={false}
        onToggleDeveloperMode={() => {}}
        llmOk={true}
        onOpenKeys={() => {}}
        onOpenConfig={() => {}}
      />,
    );
    expect(screen.getByTestId("chat-gear-menu")).toBeTruthy();
    expect(screen.getByTestId("chat-gear-keys")).toBeTruthy();
    expect(screen.getByTestId("chat-gear-config")).toBeTruthy();
    expect(screen.getByTestId("chat-gear-devmode")).toBeTruthy();
    expect(screen.getByTestId("chat-gear-llm-status").textContent).toMatch(/ready/i);
  });

  it("calls onOpenKeys and onClose when API Keys is clicked", () => {
    const onOpenKeys = vi.fn();
    const onClose = vi.fn();
    render(
      <DeveloperModeMenu
        open={true}
        onClose={onClose}
        developerMode={false}
        onToggleDeveloperMode={() => {}}
        llmOk={true}
        onOpenKeys={onOpenKeys}
        onOpenConfig={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("chat-gear-keys"));
    expect(onOpenKeys).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onOpenConfig and onClose when Model config is clicked", () => {
    const onOpenConfig = vi.fn();
    const onClose = vi.fn();
    render(
      <DeveloperModeMenu
        open={true}
        onClose={onClose}
        developerMode={false}
        onToggleDeveloperMode={() => {}}
        llmOk={true}
        onOpenKeys={() => {}}
        onOpenConfig={onOpenConfig}
      />,
    );
    fireEvent.click(screen.getByTestId("chat-gear-config"));
    expect(onOpenConfig).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onToggleDeveloperMode when the dev-mode toggle is clicked", () => {
    const onToggle = vi.fn();
    render(
      <DeveloperModeMenu
        open={true}
        onClose={() => {}}
        developerMode={false}
        onToggleDeveloperMode={onToggle}
        llmOk={true}
        onOpenKeys={() => {}}
        onOpenConfig={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("chat-gear-devmode"));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("Escape closes the menu", () => {
    const onClose = vi.fn();
    render(
      <DeveloperModeMenu
        open={true}
        onClose={onClose}
        developerMode={false}
        onToggleDeveloperMode={() => {}}
        llmOk={true}
        onOpenKeys={() => {}}
        onOpenConfig={() => {}}
      />,
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("outside-click closes the menu", () => {
    const onClose = vi.fn();
    render(
      <div>
        <div data-testid="outside">outside</div>
        <DeveloperModeMenu
          open={true}
          onClose={onClose}
          developerMode={false}
          onToggleDeveloperMode={() => {}}
          llmOk={true}
          onOpenKeys={() => {}}
          onOpenConfig={() => {}}
        />
      </div>,
    );
    fireEvent.mouseDown(screen.getByTestId("outside"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("clicking inside the menu does not close it", () => {
    const onClose = vi.fn();
    render(
      <DeveloperModeMenu
        open={true}
        onClose={onClose}
        developerMode={false}
        onToggleDeveloperMode={() => {}}
        llmOk={true}
        onOpenKeys={() => {}}
        onOpenConfig={() => {}}
      />,
    );
    fireEvent.mouseDown(screen.getByTestId("chat-gear-devmode"));
    // Outside-click should NOT have fired.
    expect(onClose).not.toHaveBeenCalled();
  });

  it("reflects developerMode=true with the is-on class on the switch", () => {
    const { container } = render(
      <DeveloperModeMenu
        open={true}
        onClose={() => {}}
        developerMode={true}
        onToggleDeveloperMode={() => {}}
        llmOk={true}
        onOpenKeys={() => {}}
        onOpenConfig={() => {}}
      />,
    );
    const sw = container.querySelector(".chat-gear-menu-switch");
    expect(sw?.className).toContain("is-on");
  });
});
