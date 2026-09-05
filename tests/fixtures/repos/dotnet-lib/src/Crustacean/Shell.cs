namespace Crustacean;

/// <summary>A shell that can be hardened.</summary>
public sealed class Shell
{
    private readonly List<string> _layers = new();

    public IReadOnlyList<string> Layers => _layers;

    public void Harden(string? layer)
    {
        if (string.IsNullOrWhiteSpace(layer))
        {
            return;
        }

        _layers.Add(layer);
    }
}
