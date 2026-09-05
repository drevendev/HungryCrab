namespace Crustacean;

/// <summary>Molting replaces the shell.</summary>
public static class Molt
{
    public static Shell Perform(Shell old)
    {
        ArgumentNullException.ThrowIfNull(old);
        return new Shell();
    }
}
